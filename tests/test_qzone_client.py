"""QQ likes may acknowledge success with a non-object data field."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, [], {}])
async def test_like_success_with_null_or_array_data_does_not_crash(data):
    from qzone_vendor.core.qzone.client import QzoneHttpClient
    from qzone_vendor.core.qzone.parser import QzoneParser

    class Response:
        status = 200

        async def text(self):
            return "unused"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Http:
        def request(self, *args, **kwargs):
            return Response()

    class Session:
        async def get_ctx(self):
            return SimpleNamespace(headers=lambda: {}, cookies=lambda: {})

    client = QzoneHttpClient.__new__(QzoneHttpClient)
    client.session, client._session = Session(), Http()
    original = QzoneParser.parse_response
    try:
        QzoneParser.parse_response = staticmethod(
            lambda text: {"code": 0, "data": data}
        )
        result = await client.request("POST", "https://example.invalid")
        assert result["code"] == 0
    finally:
        QzoneParser.parse_response = staticmethod(original)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag,expected", [(0, False), (1, True), ("1", True), (None, None)])
async def test_like_uses_page_resource_key_and_reads_actual_like_flag(flag, expected):
    from qzone_vendor.core.qzone.api import QzoneAPI

    calls = []

    async def context():
        return SimpleNamespace(uin=67890, gtk2=123)

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"code": 0, "data": {"is_dolike": flag}}

    api = QzoneAPI.__new__(QzoneAPI)
    api.session = SimpleNamespace(get_ctx=context)
    api.request = request
    post = SimpleNamespace(uin=11111, tid="target")
    assert (await api.like(post)).ok
    assert await api.like_state(post) is expected
    assert calls[0][1].startswith("https://")
    assert calls[0][2]["data"]["unikey"] == "http://user.qzone.qq.com/11111/mood/target"
    assert calls[1][2]["params"]["unikey"] == calls[0][2]["data"]["unikey"]
