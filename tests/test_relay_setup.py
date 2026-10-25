import importlib.util
import io
import json
import sys
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import settings
import relay_setup


@pytest.fixture
def setup(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(relay_setup, "ROOT", tmp_path)
    path = Path(__file__).resolve().parents[1] / "app/setup_server.py"
    spec = importlib.util.spec_from_file_location("test_relay_server", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    handler = server.Handler.__new__(server.Handler)
    handler.allowed_host = lambda: True
    responses = []
    handler.response = lambda body, status=200, **kwargs: responses.append(
        (body, status)
    )
    return tmp_path, server, handler, responses


def post(server, handler, fields, csrf=True):
    if csrf:
        fields = dict(fields, csrf=server.CSRF)
    body = urlencode(fields).encode("utf-8")
    handler.path = "/relay/save"
    handler.headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Content-Length": str(len(body)),
    }
    handler.rfile = io.BytesIO(body)
    handler.do_POST()


def fields(**changes):
    return dict(
        base_url="https://zrocode.cc/v1/",
        model="deepseek-v4.1-flash",
        relay_key="test-relay-secret-123456",
        **changes,
    )


def test_relay_save_encrypts_separately_without_activating_or_exposing(setup):
    root, server, handler, responses = setup
    settings.save_key("official-secret-123456")
    official_bytes = (root / "config/api-key.dpapi").read_bytes()
    cfg = settings.read_settings()
    cfg.update(live=True, owner_qq="11111", bot_qq="22222")
    settings.atomic_json(root / "config/user.json", cfg)
    original_cfg = (root / "config/user.json").read_bytes()
    post(server, handler, fields())
    assert responses[-1][1] == 200
    assert (root / "config/api-key.dpapi").read_bytes() == official_bytes
    assert (root / "config/user.json").read_bytes() == original_cfg
    assert settings.load_key() == "official-secret-123456"
    assert settings.load_key(relay=True) == fields()["relay_key"]
    encrypted = (root / "config/relay-api-key.dpapi").read_bytes()
    assert fields()["relay_key"].encode() not in encrypted
    saved = json.loads((root / "config/relay.json").read_text(encoding="utf-8"))
    assert saved == dict(base_url="https://zrocode.cc/v1", model="deepseek-v4.1-flash")
    handler.path = "/relay"
    handler.do_GET()
    assert "当前仍使用官方模型" in responses[-1][0]
    assert "test-relay-secret" not in responses[-1][0]
    handler.path = "/status"
    handler.do_GET()
    assert "test-relay-secret" not in responses[-1][0]
    assert "official-secret" not in responses[-1][0]


def test_blank_key_keeps_existing_key(setup):
    _, server, handler, responses = setup
    post(server, handler, fields())
    value = fields()
    value.update(relay_key="", model="another-model")
    post(server, handler, value)
    assert responses[-1][1] == 200
    assert settings.load_key(relay=True) == fields()["relay_key"]
    assert relay_setup.read_relay()["model"] == "another-model"


@pytest.mark.parametrize(
    "change",
    [
        dict(base_url="http://example.com/v1"),
        dict(base_url="https://user:password@example.com/v1"),
        dict(base_url="https://example.com/v1?key=secret"),
        dict(base_url="https://example.com:invalid/v1"),
        dict(model="bad model"),
        dict(relay_key="short"),
        dict(relay_key=""),
    ],
)
def test_invalid_submission_does_not_save_any_credential(setup, change):
    root, server, handler, responses = setup
    value = fields()
    value.update(change)
    post(server, handler, value)
    assert responses[-1][1] == 400
    assert not (root / "config/relay-api-key.dpapi").exists()
    assert not (root / "config/relay.json").exists()


def test_csrf_is_required_and_main_page_links_to_relay(setup):
    root, server, handler, responses = setup
    post(server, handler, fields(), csrf=False)
    assert responses[-1][1] == 403
    assert not (root / "config/relay-api-key.dpapi").exists()
    handler.path = "/"
    handler.do_GET()
    assert 'href="/relay"' in responses[-1][0]
