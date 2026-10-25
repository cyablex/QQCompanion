import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from engine import Engine  # noqa: E402
from model_billing import model_rates  # noqa: E402
from relay_fallback import relay_blocked, relay_filter_error  # noqa: E402
from store import BudgetExceeded, Store  # noqa: E402

BLOCK = "抱歉，系统检测到您当前输入的信息存在敏感内容，我无法响应您的请求，请检查后重新输入。"


@pytest.fixture
def setup(tmp_path):
    cfg = dict(
        model_route="relay",
        relay_connection=dict(
            pricing_cny=dict(input_other=0.6, input_cached=0.012, output=2.4)
        ),
        owner_qq="11111",
        bot_qq="22222",
        live=True,
        paused=False,
        revision=1,
        guests=True,
        guest_ids=["33333"],
        guest_mode="allowlist",
        name="大肥鱼",
        owner_title="主人",
        persona="自然简短",
        daily_calls=1000,
        daily_cny=3,
        monthly_cny=60,
        memory_enabled=False,
        digital_activities=False,
    )
    store = Store(tmp_path / "fallback.sqlite3")
    yield cfg, store
    store.db.close()


def test_filter_detection_is_specific_to_service_reply_and_error_code():
    assert relay_blocked(BLOCK)
    assert not relay_blocked("她说：“" + BLOCK + "”")
    assert not relay_blocked("这个问题涉及敏感内容，我不想聊。")
    assert not relay_blocked('{"messages":["' + BLOCK + '"]}')
    assert not relay_filter_error(TimeoutError())
    exc = RuntimeError()
    exc.body = dict(error=dict(code="content_filter"))
    assert relay_filter_error(exc)
    exc.body = dict(error=dict(code="invalid_api_key"))
    assert not relay_filter_error(exc)


@pytest.mark.asyncio
async def test_aux_charges_both_calls_correctly_and_next_request_returns_to_relay(
    setup,
):
    cfg, store = setup
    calls = []

    async def generate(system, contexts, response_mode="json", official_fallback=False):
        calls.append(official_fallback)
        if len(calls) == 1:
            return BLOCK, dict(
                input_other=100,
                input_cached=0,
                output=10,
                billing_rates=model_rates(cfg),
            )
        return "已完成", dict(
            input_other=1000,
            input_cached=100,
            output=100,
            billing_rates=model_rates(
                dict(cfg, model_route="official") if official_fallback else cfg
            ),
        )

    engine = Engine(store, generate, None, lambda: dict(cfg))
    assert await engine.generate_aux("memory", "整理", []) == "已完成"
    assert await engine.generate_aux("memory", "再整理", []) == "已完成"
    assert calls == [False, True, False]
    rows = store.db.execute(
        "SELECT kind,amount,state FROM calls ORDER BY created"
    ).fetchall()
    assert [r["kind"] for r in rows] == ["memory", "memory_official_fallback", "memory"]
    assert [r["amount"] for r in rows] == pytest.approx([0.000084, 0.002804, 0.0008412])
    assert all(r["state"] == "completed" for r in rows)
    assert store.get("official_fallback_stats")["attempts"] == 1


@pytest.mark.asyncio
async def test_official_reservation_obeys_same_daily_cap(setup):
    cfg, store = setup
    cfg["daily_cny"] = 0.05
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs)
        return BLOCK, None

    engine = Engine(store, generate, None, lambda: dict(cfg))
    with pytest.raises(BudgetExceeded):
        await engine.generate_aux("memory", "s", [])
    assert len(calls) == 1
    row = store.db.execute("SELECT amount FROM calls").fetchone()
    assert row[0] > 0
    assert not store.get("official_fallback_stats")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError, PermissionError, asyncio.CancelledError]
)
async def test_non_filter_failures_do_not_fallback(setup, error):
    cfg, store = setup
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs)
        raise error()

    engine = Engine(store, generate, None, lambda: dict(cfg))
    with pytest.raises(error):
        await engine.generate_aux("memory", "s", [])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_structured_filter_error_falls_back_once_and_retains_unknown_reserve(
    setup,
):
    cfg, store = setup
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs.get("official_fallback", False))
        if len(calls) == 1:
            exc = RuntimeError("provider filtered")
            exc.body = dict(error=dict(type="content_filter"))
            raise exc
        raise PermissionError("official unavailable")

    engine = Engine(store, generate, None, lambda: dict(cfg))
    with pytest.raises(PermissionError):
        await engine.generate_aux("memory", "s", [])
    assert calls == [False, True]
    rows = store.db.execute("SELECT amount FROM calls").fetchall()
    assert len(rows) == 2 and all(r[0] > 0 for r in rows)


@pytest.mark.asyncio
async def test_stale_configuration_does_not_pay_for_fallback(setup):
    cfg, store = setup
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs)
        cfg["revision"] += 1
        return BLOCK, None

    engine = Engine(store, generate, None, lambda: dict(cfg))
    with pytest.raises(RuntimeError, match="stale"):
        await engine.generate_aux("memory", "s", [])
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["new_message", "forget"])
async def test_changed_contact_context_cancels_fallback(setup, change):
    cfg, store = setup
    store.incoming("one", "33333", "你好")
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs)
        if change == "forget":
            store.forget("33333")
        else:
            store.incoming("two", "33333", "新问题")
        return BLOCK, None

    engine = Engine(store, generate, None, lambda: dict(cfg))
    with pytest.raises(RuntimeError, match="stale"):
        await engine.decision("33333", "reply", "回复")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_same_question_stays_official_across_search_and_read_then_resets(setup):
    cfg, store = setup
    cfg["web_search"] = True
    store.incoming("one", "33333", "查一下公开资料")
    calls = []
    official_answers = iter(["[搜索:公开资料]", "[阅读:1]", "已经查到公开依据。"])

    async def generate(system, contexts, response_mode="json", official_fallback=False):
        calls.append(official_fallback)
        text = (
            next(official_answers)
            if official_fallback
            else (BLOCK if len(calls) == 1 else "下一题用中转。")
        )
        return text, dict(input_other=10, input_cached=0, output=10)

    async def lookup(*args):
        return dict(
            sources=[
                dict(
                    title="来源",
                    url="https://example.org/article",
                    excerpt="摘要",
                    kind="search_excerpt",
                )
            ]
        )

    async def read(*args):
        return dict(
            title="来源",
            url="https://example.org/article",
            excerpt="正文",
            kind="page_excerpt",
        )

    engine = Engine(store, generate, None, lambda: dict(cfg))
    engine.web.lookup = lookup
    engine.web.read_source = read
    decision = await engine.decision("33333", "reply", "回复")
    assert "已经查到" in "".join(decision["messages"]) and BLOCK not in "".join(
        decision["messages"]
    )
    assert calls == [False, True, True, True]
    store.incoming("two", "33333", "下一个问题")
    decision = await engine.decision("33333", "reply", "回复")
    assert calls == [False, True, True, True, False]
    assert decision["messages"] == ["下一题用中转。"]


@pytest.mark.asyncio
async def test_only_official_answer_is_delivered_once(setup):
    cfg, store = setup
    store.incoming("one", "33333", "今天想安静待着")
    sent = []

    async def generate(system, contexts, response_mode="json", official_fallback=False):
        return ("那就歇会儿吧。" if official_fallback else BLOCK), None

    async def send(contact, text, **kwargs):
        sent.append(text)
        return {"message_id": "test-receipt"}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    decision = await engine.decision("33333", "reply", "回复")
    await engine.deliver("33333", "reply", decision, key="fallback-once")
    await engine.deliver("33333", "reply", decision, key="fallback-once")
    assert sent == ["那就歇会儿吧。"]
    assert BLOCK not in str(store.history("33333"))
