import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from engine import Engine
from store import Store
from web_lookup import WebLookup, public_url


@pytest.fixture
def env(tmp_path):
    cfg = dict(owner_qq="11111", bot_qq="22222", name="大肥鱼", persona="自然聊天", revision=1,
               live=True, paused=False, guests=True, guest_mode="open", web_search=True,
               daily_calls=400, daily_cny=3, monthly_cny=60, web_daily_requests=60)
    store = Store(tmp_path / "web.sqlite3")
    prompts, sent, http = [], [], []
    outputs = []

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        return outputs.pop(0), {"input_other": 1, "output": 1}

    async def send(contact, text, **options):
        sent.append((contact, text))
        return {"message_id": len(sent)}

    def request(endpoint, payload):
        http.append((endpoint, payload))
        if endpoint == "fetch":
            return {"content": "实际官网正文"}
        return {"results": [{"title": "官方说明", "url": "https://example.org/official", "content": "公开答案。"}]}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    engine.web = WebLookup(store, request)
    yield store, cfg, engine, outputs, prompts, sent, http
    store.db.close()


@pytest.mark.asyncio
async def test_real_search_flow_adds_sources_without_exposing_other_contact(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("other", "33333", "OTHER_CONTACT_SECRET")
    store.incoming("question", "11111", "查一下这个工具的最新版本")
    outputs.extend(["[搜索:公开工具 最新版本 官方]", "[阅读:1]", "我查到官方的新说明了。"])
    assert await engine.reply("11111")
    assert len(prompts) == 3 and len(http) == 2 and len(sent) == 1
    assert http[0][1] == {"query": "公开工具 最新版本 官方", "count": 5, "need_content": True}
    assert "实际官网正文" in prompts[2][0] and "OTHER_CONTACT_SECRET" not in str(prompts)
    assert "https://example.org/official" in sent[0][1]
    assert store.get("web_lookup:22222:11111")["sources"]
    assert store.get("web_lookup:22222:33333") is None
    assert store.db.execute("SELECT amount FROM calls WHERE kind='web_search'").fetchone()[0] == 0.005
    store.forget("11111")
    assert store.get("web_lookup:22222:11111") is None


@pytest.mark.asyncio
async def test_casual_chat_does_not_make_web_request(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("chat", "11111", "今天有点累")
    outputs.append("先歇一会儿吧。")
    assert await engine.reply("11111")
    assert len(prompts) == 1 and http == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "network", "disabled", "budget"])
async def test_search_failure_is_explicit_and_never_fakes_success(env, failure):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("question", "11111", "帮我搜索最新信息")
    outputs.append("[搜索:公开问题]")
    if failure == "empty":
        engine.web.request = lambda *args: {"results": []}
    elif failure == "network":
        def fail(*args):
            raise OSError("network unavailable")
        engine.web.request = fail
    elif failure == "disabled":
        cfg["web_search"] = False
    else:
        cfg["web_daily_requests"] = 0
    assert await engine.reply("11111")
    assert len(prompts) == 1 and len(sent) == 1
    assert "不能" in sent[0][1] or "没查到" in sent[0][1]
    assert "https://" not in sent[0][1]


@pytest.mark.asyncio
async def test_new_message_or_forgetting_during_search_cancels_old_answer(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("old-question", "11111", "搜索这个")
    outputs.append("[搜索:公开问题]")

    async def lookup(*args):
        store.forget("11111")
        return {"sources": [{"url": "https://example.org", "excerpt": "old"}]}

    engine.web.lookup = lookup
    assert not await engine.reply("11111")
    assert sent == [] and store.get("web_lookup:22222:11111") is None


@pytest.mark.asyncio
async def test_fetch_fills_missing_body_and_cost_is_counted(env):
    store, cfg, engine, outputs, prompts, sent, http = env

    def request(endpoint, payload):
        http.append((endpoint, payload))
        return {"results": [{"title": "官方", "url": "https://example.org/body"}]} if endpoint == "search" else {"content": "实际页面正文"}

    engine.web.request = request
    lookup = await engine.web.lookup("公开信息", cfg, "11111")
    source = await engine.web.read_source(lookup["sources"][0]["url"], cfg)
    assert source["excerpt"] == "实际页面正文"
    assert [row[0] for row in http] == ["search", "fetch"]
    assert store.db.execute("SELECT SUM(amount) FROM calls").fetchone()[0] == 0.01


@pytest.mark.asyncio
async def test_account_identifiers_never_reach_search_provider(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    for query in ["11111 私人信息", "手机号 13812345678", "22222"]:
        with pytest.raises(ValueError):
            await engine.web.lookup(query, cfg, "11111")
    assert http == []


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/a", "http://10.0.0.1/", "file:///secret", "https://user:pass@example.org/a"])
def test_local_or_credential_urls_are_not_fetch_targets(url):
    assert not public_url(url)


@pytest.mark.asyncio
async def test_model_selects_second_source_and_can_supplement_once(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    searches = 0

    def request(endpoint, payload):
        nonlocal searches
        http.append((endpoint, payload))
        if endpoint == "search":
            searches += 1
            return {"results": [dict(title="不相关", url="https://example.org/unrelated", content="不相关摘要"), dict(title="相关来源", url="https://example.org/relevant", content="相关摘要")] if searches == 1 else [dict(title="核查来源", url="https://example.org/verify", content="补充依据")]}
        return dict(content="真正选中的正文：" + payload["url"])

    engine.web.request = request
    store.incoming("question", "11111", "帮我查证这个公开问题")
    outputs.extend(["[搜索:公开问题]", "[阅读:2]", "[补查:公开问题 时间依据]", "[阅读:3]", "两份资料说明了适用时间的区别。"])
    assert await engine.reply("11111")
    assert [payload["url"] for name, payload in http if name == "fetch"] == ["https://example.org/relevant", "https://example.org/verify"]
    assert len(http) == 4 and len(prompts) == 5 and len(sent) == 1
    assert "https://example.org/relevant" in sent[0][1]
    assert store.db.execute("SELECT SUM(amount) FROM calls WHERE kind IN ('web_search','web_fetch')").fetchone()[0] == 0.02


@pytest.mark.asyncio
async def test_third_search_is_blocked_without_leaking_control_marker(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("question", "11111", "查证这个问题")
    outputs.extend(["[搜索:公开问题]", "[补查:进一步公开问题]", "[补查:还要继续搜索]"])
    assert await engine.reply("11111")
    assert len(http) == 2 and "[补查" not in sent[0][1]
    assert "不能" in sent[0][1] or "不把" in sent[0][1]


@pytest.mark.asyncio
async def test_shared_article_reads_without_search_and_persists_only_for_sender(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("link", "11111", "看看这篇 https://example.org/article，聊聊你感兴趣的细节")
    outputs.append("这里这个比喻很有意思。")
    assert await engine.reply("11111")
    assert http == [("fetch", dict(url="https://example.org/article"))]
    assert "实际官网正文" in prompts[0][0]
    assert store.get("web_readings:22222:11111")
    assert not store.get("web_readings:22222:33333")
    assert not engine.activities.public_records(cfg)
    store.incoming("follow-up", "11111", "接着聊这篇文章")
    outputs.append("刚才那篇里的细节还可以再想想。")
    assert await engine.reply("11111")
    assert len(http) == 1 and "实际官网正文" in prompts[-1][0]
    store.incoming("other", "33333", "这篇文章有什么意思")
    outputs.append("你说的是哪篇？")
    assert await engine.reply("33333")
    assert "实际官网正文" not in prompts[-1][0]
    outputs.append('{"action":"skip","messages":[]}')
    await engine.decision("11111", "qzone_post", "聊聊最近阅读", public=True)
    assert "实际官网正文" not in prompts[-1][0]
    store.forget("11111")
    assert store.get("web_readings:22222:11111") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["forget", "new_message", "pause"])
async def test_obsolete_shared_read_never_sends_or_saves(env, mutation):
    store, cfg, engine, outputs, prompts, sent, http = env
    store.incoming("link", "11111", "看看 https://example.org/article")

    async def read(*args):
        if mutation == "forget":
            store.forget("11111")
        elif mutation == "new_message":
            store.incoming("new", "11111", "先不看了")
        else:
            cfg.update(paused=True, revision=2)
        return dict(url="https://example.org/article", title="测试", excerpt="旧正文", kind="page_excerpt")

    engine.web.read_source = read
    assert not await engine.reply("11111")
    assert sent == prompts == [] and store.get("web_readings:22222:11111") is None


@pytest.mark.asyncio
async def test_failed_chosen_page_is_not_claimed_as_read(env):
    store, cfg, engine, outputs, prompts, sent, http = env

    def request(endpoint, payload):
        http.append((endpoint, payload))
        return dict(results=[dict(url="https://example.org/article", title="文章", content="只有搜索摘要")]) if endpoint == "search" else dict(content="")

    engine.web.request = request
    store.incoming("question", "11111", "帮我查这个问题")
    outputs.extend(["[搜索:公开问题]", "[阅读:1]", "没能读到正文，只能先参考摘要。"])
    assert await engine.reply("11111")
    assert "正文未能读取" in prompts[-1][0]
    assert not store.get("web_readings:22222:11111")
    assert store.get("web_lookup:22222:11111")["sources"][0]["kind"] == "search_excerpt"


@pytest.mark.asyncio
async def test_authenticated_or_local_shared_link_never_reaches_fetch(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    for i, url in enumerate(["http://127.0.0.1/private", "https://example.org/article?access_token=SECRET"]):
        store.incoming(str(i), "11111", "读一下 " + url)
        outputs.append("这个链接没能读取，请贴公开正文。")
        assert await engine.reply("11111")
    assert http == [] and not store.get("web_readings:22222:11111")


@pytest.mark.asyncio
async def test_supplemental_search_cancelled_by_new_message(env):
    store, cfg, engine, outputs, prompts, sent, http = env
    original = engine.web.lookup
    count = 0

    async def lookup(*args):
        nonlocal count
        count += 1
        result = await original(*args)
        if count == 2:
            store.incoming("new", "11111", "先别查了")
        return result

    engine.web.lookup = lookup
    store.incoming("question", "11111", "查一下这个问题")
    outputs.extend(["[搜索:公开问题]", "[补查:另一个公开问题]"])
    assert not await engine.reply("11111")
    assert sent == []
