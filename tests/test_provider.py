"""Check the actual AstrBot -> OpenAI HTTP body without a real API request."""

import json
import sys
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "astrbot"))

from prepare_runtime import DEEPSEEK_BODY, DEEPSEEK_TEXT_BODY  # noqa: E402
from astrbot.core.agent.message import TextPart, UserMessageSegment  # noqa: E402
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [DEEPSEEK_BODY, DEEPSEEK_TEXT_BODY])
async def test_output_options_reach_actual_http_body(body):
    captured = []

    async def transport(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "Internal reasoning, never a QQ reply.",
                            "content": '{"action":"reply","messages":["hi"]}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 105,
                    "completion_tokens_details": {"reasoning_tokens": 100},
                    "total_tokens": 117,
                },
            },
        )

    config = {
        "id": "test",
        "type": "openai_chat_completion",
        "key": ["test-placeholder"],
        "api_base": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        "custom_extra_body": body,
    }
    provider = ProviderOpenAIOfficial(config, {})
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="test-placeholder",
        base_url=config["api_base"],
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    try:
        result = await provider.text_chat(
            prompt=None,
            system_prompt="Return JSON.",
            contexts=[UserMessageSegment(content=[TextPart(text="hi")])],
            request_max_retries=1,
        )
        assert json.loads(result.completion_text)["action"] == "reply"
        assert len(captured) == 1
        assert captured[0]["response_format"] == body["response_format"]
        assert captured[0]["max_tokens"] == 8192
        assert captured[0]["thinking"] == {"type": "enabled"}
        assert captured[0]["reasoning_effort"] == "low"
        assert "Internal reasoning" not in result.completion_text
        assert result.usage.output == 105
        assert captured[0]["stream"] is False
    finally:
        await provider.client.close()
