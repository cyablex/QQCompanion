"""Resolve a named public Bilibili recording and download its audio track."""

import html
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from song_library import normalized, artist_key

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}
_session = None


class SourceTemporary(ValueError):
    """A source request can be retried after cooling down."""

    def __init__(self, message, stage="source"):
        super().__init__(message)
        self.stage = stage


class SourceNeeded(ValueError):
    """The requester must identify a usable recording."""


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        # Only public visitor cookies; no browser login or account cookies.
        try:
            _session.get("https://www.bilibili.com/", timeout=8)
        except requests.RequestException:
            pass
    return _session


def api(path, params):
    stage = "search" if "search" in path else "audio" if "playurl" in path else "source"
    label = {"search": "搜索", "audio": "音轨读取", "source": "视频读取"}[stage]
    for attempt in range(2):
        try:
            response = session().get("https://api.bilibili.com" + path, params=params, timeout=(8, 15))
            if response.status_code in {412, 429}:
                raise SourceTemporary(f"B站{label}暂时被限制（{response.status_code}）。", stage)
            if response.status_code >= 500:
                raise SourceTemporary(f"B站{label}服务暂时不可用。", stage)
            response.raise_for_status()
            data = response.json()
            if data.get("code") in {-352, -412, -509, -799}:
                raise SourceTemporary(f"B站{label}暂时被限制。", stage)
            if data.get("code") != 0:
                raise SourceNeeded(f"B站{label}没有提供可用内容，可能需要登录或更换单曲链接。")
            return data.get("data") or {}
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, SourceNeeded):
                raise
            error = exc if isinstance(exc, SourceTemporary) else SourceTemporary(f"B站{label}连接暂时未成功。", stage)
            if attempt:
                raise error from exc
            time.sleep(3)


def video_id(url):
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname == "b23.tv" and re.fullmatch(r"/[0-9A-Za-z]+/?", parts.path):
        try:
            response = session().get(url, allow_redirects=False, timeout=(8, 12))
            location = response.headers.get("Location", "")
            if urlsplit(location).hostname in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
                return video_id(location)
        except requests.RequestException as exc:
            raise SourceTemporary("B站分享链接暂时未能打开。") from exc
        raise SourceNeeded("这条分享链接没有指向可读取的B站单曲视频，请补完整视频链接。")
    if parts.scheme == "https" and parts.hostname in {"www.bilibili.com", "bilibili.com", "m.bilibili.com"}:
        match = re.search(r"/video/(BV[0-9A-Za-z]{10})(?:/|$)", parts.path)
        if match:
            return match[1]
    raise SourceNeeded("目前需要可读取的 B 站单曲音源链接。")


def resolve(title, artist, source_url="", *, verify_source=False):
    if source_url:
        bvid = video_id(source_url)
    else:
        data = api("/x/web-interface/search/type", dict(search_type="video", keyword=f"{artist} {title} 完整版", page=1, page_size=20))
        candidates = []
        for item in data.get("result") or []:
            name = html.unescape(re.sub(r"<[^>]+>", "", item.get("title", "")))
            if not item.get("bvid") or normalized(title) not in normalized(name) or artist_key(artist) not in normalized(name):
                continue
            if re.search(r"教学|教程|合集|串烧|纯音乐|伴奏|指弹|乐器|反应|reaction", name, re.I):
                continue
            score = sum(word in name for word in ("完整", "原唱", "原曲", "官方", "音质")) - 2 * bool(re.search(r"翻唱|cover|片段|混剪", name, re.I))
            candidates.append((score, item["bvid"]))
        if not candidates:
            raise SourceNeeded("没有找到能明确对应这位歌手或版本的音源，发我具体的 B 站单曲链接再试吧。")
        bvid = max(candidates, key=lambda row: row[0])[1]
    detail = api("/x/web-interface/view", dict(bvid=bvid))
    if (not source_url or verify_source) and not matches(title, artist, detail.get("title", "")):
        raise SourceNeeded("备用检索找到的视频没有明确对应歌名和歌手或版本，请给我具体的B站单曲链接。")
    if not 5 <= detail.get("duration", 0) <= 600 or len(detail.get("pages") or []) != 1:
        raise SourceNeeded("这条音源不是 10 分钟以内的单首歌曲，请换一个完整单曲链接。")
    return dict(url=f"https://www.bilibili.com/video/{bvid}/", bvid=bvid, cid=detail["cid"],
                title=detail.get("title", ""), uploader=(detail.get("owner") or {}).get("name", ""), duration=detail["duration"])


def matches(title, artist, name):
    name = html.unescape(re.sub(r"<[^>]+>", "", name))
    return bool(normalized(title) in normalized(name) and artist_key(artist) in normalized(name)
                and not re.search(r"教学|教程|合集|串烧|纯音乐|伴奏|指弹|乐器|反应|reaction", name, re.I))


def download(source, destination, cancelled=lambda: False):
    data = api("/x/player/playurl", dict(bvid=source["bvid"], cid=source["cid"], fnval=16, qn=64))
    audio = (data.get("dash") or {}).get("audio") or []
    if audio:
        chosen = max(audio, key=lambda a: a.get("bandwidth", 0))
        url = chosen.get("baseUrl") or chosen.get("base_url")
    elif data.get("durl"):
        url = data["durl"][0]["url"]
    else:
        raise SourceNeeded("没有取得可读取的歌曲音轨，可能需要登录或更换音源。")
    host = urlsplit(url).hostname or ""
    if not any(host.endswith("." + suffix) for suffix in ("bilivideo.com", "bilivideo.cn", "bilivideo.net", "hdslb.com")):
        raise ValueError("音源返回了未支持的音频地址。")
    total = 0
    try:
        with session().get(url, headers={"Referer": source["url"]}, stream=True, timeout=(10, 20)) as response:
            if response.status_code in {403, 412, 429}:
                raise SourceTemporary("B站音轨下载暂时被限制。", "audio")
            response.raise_for_status()
            with Path(destination).open("wb") as output:
                for chunk in response.iter_content(128 * 1024):
                    if cancelled():
                        raise InterruptedError("歌曲准备已暂停或取消。")
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise SourceNeeded("这条音轨过大，请换一个单曲音源。")
                    output.write(chunk)
    except requests.RequestException as exc:
        raise SourceTemporary("B站音轨下载连接暂时中断。", "audio") from exc
