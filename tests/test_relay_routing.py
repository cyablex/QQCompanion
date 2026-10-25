import json
import sys
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "astrbot"))
import model_billing  # noqa: E402
import prepare_runtime  # noqa: E402
import web_lookup  # noqa: E402
from engine import Engine  # noqa: E402
from store import BudgetExceeded, Store  # noqa: E402
from astrbot.core.agent.message import TextPart, UserMessageSegment  # noqa: E402
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial  # noqa: E402
from astrbot.core.provider.manager import ProviderManager  # noqa: E402


def relay_cfg():
    return dict(
        model="deepseek-flash",
        model_route="relay",
        relay_connection=dict(
            base_url="https://zrocode.cc/v1",
            model="deepseek-v4.1-flash",
            pricing_cny=dict(input_other=0.6, input_cached=0.012, output=2.4),
        ),
    )


def test_provider_selection_preserves_separate_credentials_and_verified_snapshot(
    monkeypatch,
):
    monkeypatch.setattr(prepare_runtime, "load_key", lambda **kwargs: "unused-secret")
    providers = prepare_runtime.model_providers(relay_cfg())
    assert len(providers) == 4
    for provider in providers[:2]:
        assert provider["api_base"] == "https://zrocode.cc/v1"
        assert provider["key"] == ["$QQCOMPANION_RELAY_API_KEY"]
        assert provider["model"] == "deepseek-v4.1-flash"
    for fallback in providers[2:]:
        assert fallback["api_base"] == "https://api.deepseek.com/v1"
        assert fallback["key"] == ["$QQCOMPANION_DEEPSEEK_API_KEY"]
        assert fallback["model"] == "deepseek-flash"
        assert fallback["custom_extra_body"]["reasoning_effort"] == "low"
    assert providers[0]["custom_extra_body"]["response_format"] == dict(
        type="json_object"
    )
    assert providers[1]["custom_extra_body"]["response_format"] == dict(type="text")
    official = prepare_runtime.model_providers(dict(model="deepseek-flash"))
    assert official[0]["api_base"] == "https://api.deepseek.com/v1"
    assert official[0]["key"] == ["$QQCOMPANION_DEEPSEEK_API_KEY"]
    assert "unused-secret" not in json.dumps(providers)


def test_unverified_price_cannot_activate_relay():
    cfg = relay_cfg()
    cfg["relay_connection"].pop("pricing_cny")
    with pytest.raises(ValueError, match="prices"):
        prepare_runtime.model_providers(cfg)


def test_framework_resolves_relay_environment_reference(monkeypatch):
    monkeypatch.setenv("QQCOMPANION_RELAY_API_KEY", "relay-test-only")
    monkeypatch.setattr(prepare_runtime, "load_key", lambda **kwargs: "unused")
    provider = prepare_runtime.model_providers(relay_cfg())[0]
    manager = ProviderManager.__new__(ProviderManager)
    resolved = manager._resolve_env_key_list(provider)
    assert resolved["key"] == ["relay-test-only"]


def test_cny_costs_include_cache_and_snapshot():
    usage = dict(input_other=349, input_cached=3840, output=235)
    assert model_billing.usage_cost(usage, relay_cfg()) == pytest.approx(0.00081948)
    usage["billing_rates"] = model_billing.model_rates(dict(model_route="official"))
    assert model_billing.usage_cost(usage, relay_cfg()) == pytest.approx(0.0027316)


@pytest.mark.asyncio
async def test_aux_reserves_and_settles_relay_prices_against_shared_daily_budget(
    tmp_path,
):
    cfg = relay_cfg()
    cfg.update(live=True, paused=False, daily_calls=1000, daily_cny=3, monthly_cny=60)
    store = Store(tmp_path / "budget.sqlite3")
    reservations = []

    async def generate(*args, **kwargs):
        reservations.append(
            store.db.execute(
                "SELECT amount FROM calls ORDER BY created DESC LIMIT 1"
            ).fetchone()[0]
        )
        return "ok", dict(input_other=1000, input_cached=1000, output=1000)

    engine = Engine(store, generate, None, lambda: dict(cfg))
    assert await engine.generate_aux("memory", "s", []) == "ok"
    assert reservations == pytest.approx([0.03 * 0.3 + 8192 * 2.4 / 1e6])
    assert store.status()["estimated_cny_today"] == pytest.approx(0.003, abs=0.0001)
    rid = store.reserve(cfg, "web_search", 2.98)
    store.settle(rid, 2.98)
    with pytest.raises(BudgetExceeded):
        await engine.generate_aux("memory", "s", [])
    assert len(reservations) == 1
    store.db.close()


def test_official_tools_always_use_official_key(monkeypatch):
    requested = []
    monkeypatch.setattr(web_lookup, "load_key", lambda: "official-test-key")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"results":[]}'

    def open_request(request, **kwargs):
        requested.append(request)
        return Response()

    monkeypatch.setattr(web_lookup.urllib.request, "urlopen", open_request)
    web_lookup.WebLookup._request("search", dict(query="public topic"))
    assert requested[0].full_url == "https://api.deepseek.com/v1/tools/search"
    assert requested[0].get_header("Authorization") == "Bearer official-test-key"


@pytest.mark.asyncio
async def test_relay_sdk_body_and_reasoning_not_in_visible_reply(monkeypatch):
    captured = []
    monkeypatch.setattr(prepare_runtime, "load_key", lambda **kwargs: "test-key")
    cfg = prepare_runtime.model_providers(relay_cfg())[1]

    async def transport(request):
        captured.append((str(request.url), json.loads(request.content)))
        return httpx.Response(
            200,
            json=dict(
                id="mock",
                object="chat.completion",
                created=0,
                model=cfg["model"],
                choices=[
                    dict(
                        index=0,
                        message=dict(
                            role="assistant",
                            content="你好",
                            reasoning_content="private",
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=dict(
                    prompt_tokens=100,
                    completion_tokens=20,
                    total_tokens=120,
                    prompt_tokens_details=dict(cached_tokens=60),
                ),
            ),
        )

    provider = ProviderOpenAIOfficial(cfg, {})
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="test-key",
        base_url=cfg["api_base"],
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    try:
        result = await provider.text_chat(
            prompt=None,
            system_prompt="简短回答",
            contexts=[UserMessageSegment(content=[TextPart(text="你好")])],
            request_max_retries=1,
        )
        assert result.completion_text == "你好"
        assert result.usage.input_cached == 60 and result.usage.input_other == 40
        assert captured[0][0] == "https://zrocode.cc/v1/chat/completions"
        assert captured[0][1]["thinking"] == dict(type="enabled")
        assert captured[0][1]["reasoning_effort"] == "low"
        assert captured[0][1]["max_tokens"] == 8192
    finally:
        await provider.client.close()
