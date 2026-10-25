"""Discover unread articles in the five approved public reading categories."""

import asyncio
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CATEGORIES = (
    dict(id="nature", title="动物与植物", interests="动物行为、花草、奇怪的种子、海洋", search="kib.cas.cn OR site:fisheries.noaa.gov", hosts=("kib.cas.cn", "www.fisheries.noaa.gov"), example="有趣的植物 种子"),
    dict(id="everyday", title="日常科学", interests="食物、声音、颜色、身边小物件的原理", search="kepuchina.cn", hosts=("www.kepuchina.cn", "cloud.kepuchina.cn", "piyao.kepuchina.cn"), example="日常科学 声音 颜色"),
    dict(id="space", title="天空与宇宙", interests="月亮、行星、星云、宇宙奇景", search="science.nasa.gov", hosts=("science.nasa.gov",), example="universe quick reads unusual planets", index="https://science.nasa.gov/universe/stories/quick-reads/"),
    dict(id="art", title="绘画、器物与审美", interests="纹样、生活器物、画中细节和有趣的设计", search="dpm.org.cn/collection/", hosts=("www.dpm.org.cn",), example="藏品 有趣 器物 纹样"),
    dict(id="literature", title="故事与诗歌", interests="经典童话、寓言、短诗和短篇，留意情节、幽默和表达", search="gutenberg.org/cache/epub/", hosts=("www.gutenberg.org", "gutenberg.org"), example="short fairy tale poems"),
)
BY_ID = {row["id"]: row for row in CATEGORIES}
MAX_SOURCE_CHARS = 9000


class _CatalogLinks(HTMLParser):
    def __init__(self, category):
        super().__init__()
        self.category, self.href, self.text, self.rows = category, None, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href, self.text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            url = article_url(urljoin(self.category["index"], self.href), self.category)
            title = " ".join(" ".join(self.text).split())[:160]
            if url and url.startswith(self.category["index"]) and title and url not in {row["url"] for row in self.rows}:
                self.rows.append(dict(url=url, title=title, date="未知", text=""))
            self.href = None


class _NoCatalogRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Catalog redirects are not followed")


def fetch_catalog(category):
    url = category["index"]
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Catalog did not resolve to public IPs")
    request = Request(url, headers={"User-Agent": "QQCompanion/2.0 public-reading", "Accept": "text/html"})
    with build_opener(_NoCatalogRedirect()).open(request, timeout=8) as response:
        if response.status != 200 or response.geturl() != url or response.headers.get_content_type() != "text/html":
            raise ValueError("Unexpected catalog response")
        raw = response.read(500001)
        if len(raw) > 500000:
            raise ValueError("Catalog too large")
        parser = _CatalogLinks(category)
        parser.feed(raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace"))
    return parser.rows


async def catalog_articles(engine, cfg, category):
    key = "reading_catalog:" + category["id"]
    cached = engine.web.store.get(key, {})
    if 0 <= time.time() - cached.get("at", 0) < 86400:
        return cached["rows"]
    rid = engine.web.reserve_request(cfg, "reading_catalog_fetch", 0)
    try:
        rows = await asyncio.to_thread(fetch_catalog, category)
        engine.web.store.settle(rid, 0)
        engine.web.store.set(key, dict(at=time.time(), rows=rows))
        return rows
    except BaseException:
        engine.web.store.settle(rid, state="failed_or_unknown")
        raise


def article_url(url, category):
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in category["hosts"] or parts.port not in (None, 443) or parts.username or parts.password:
            return None
        path = parts.path
        host = parts.hostname
        if host == "kib.cas.cn":
            valid = bool(re.fullmatch(r"/kxcb/kpwz/\d{6}/[^/]+\.html", path))
        elif host == "www.fisheries.noaa.gov":
            valid = bool(re.fullmatch(r"/species/[^/]+", path))
        elif host == "www.dpm.org.cn":
            valid = bool(re.fullmatch(r"/collection/[^/]+/\d+\.html", path))
        elif host in ("www.gutenberg.org", "gutenberg.org"):
            valid = bool(re.fullmatch(r"/(?:cache/epub|files)/\d+/[^/]+\.(?:html?|txt)", path))
        elif host == "science.nasa.gov":
            valid = path.count("/") >= 3 and path.rstrip("/").split("/")[-1] not in {"stories", "quick-reads"}
        else:
            valid = ("/article/" in path or "/newSearch/imgText" in path or "/rumordetail" in path or bool(re.search(r"/t\d+_\d+\.(?:shtml|html)$", path)))
        if not valid or re.search(r"\.(?:pdf|png|jpg|mp4)$", path, re.I):
            return None
        return urlunsplit(("https", host, path, parts.query, ""))
    except (ValueError, TypeError):
        return None


def clean_query(query, cfg):
    query = re.sub(r"site:\S+", "", query).strip()
    if not query or len(query) > 100 or re.search(r"\d{7,}|https?://|[\r\n\x00-\x1f]", query) or any(str(cfg.get(key, "")) in query for key in ("owner_qq", "bot_qq") if cfg.get(key)):
        raise ValueError("Reading search must contain only a public topic")
    return query


async def discover_article(engine, cfg, category_id, query, seen):
    if not cfg.get("web_search", False):
        raise RuntimeError("Web reading disabled")
    category = BY_ID[category_id]
    query = clean_query(query, cfg)
    response = await engine.web.call(cfg, "search", dict(query=query + " site:" + category["search"], count=10, need_content=True))
    fresh = engine.config_reader()
    if fresh.get("revision") != cfg["revision"] or fresh.get("paused") or not fresh.get("live") or not fresh.get("web_search") or not fresh.get("digital_activities", True) or fresh.get("bot_qq") != cfg.get("bot_qq"):
        raise RuntimeError("Reading configuration changed")
    candidates = []
    for item in response.get("results", []):
        if not isinstance(item, dict):
            continue
        url = article_url(item.get("url", ""), category)
        if url and url not in seen and url not in {row["url"] for row in candidates}:
            candidates.append(dict(title=str(item.get("title", ""))[:160], url=url, date=str(item.get("date") or "未知"), text=str(item.get("content") or item.get("summary") or item.get("snippet") or "")[:2200]))
    if not candidates and category.get("index"):
        candidates = [row for row in await catalog_articles(engine, cfg, category) if row["url"] not in seen]
    if not candidates:
        raise ValueError("No unread article from the approved sources")
    fresh = engine.config_reader()
    if fresh.get("revision") != cfg["revision"] or fresh.get("paused") or not fresh.get("live") or not fresh.get("web_search"):
        raise RuntimeError("Reading configuration changed")
    source = candidates[0]
    page = await engine.web.call(cfg, "fetch", dict(url=source["url"]))
    body = str(page.get("content") or "").strip()
    scope = "page_excerpt" if body else "search_excerpt"
    body = body or source["text"]
    if len(body) < 60:
        raise ValueError("No usable source excerpt")
    return dict(title=source["title"], url=source["url"], date=source["date"], category=category_id, query=query, text=body[:MAX_SOURCE_CHARS], scope=scope, retrieved_at=time.time(), truncated=len(body) > MAX_SOURCE_CHARS)
