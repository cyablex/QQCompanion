"""Generate isolated framework configuration without plaintext API credentials."""

import copy
import json
import os
import shutil
import sys

from settings import (
    ROOT,
    MODEL_MAX_OUTPUT_TOKENS,
    MODEL_REQUEST_TIMEOUT,
    atomic_json,
    load_key,
    local_token,
    read_settings,
)

DEEPSEEK_BODY = {
    "thinking": {"type": "enabled"},
    "reasoning_effort": "low",
    "response_format": {"type": "json_object"},
    "max_tokens": MODEL_MAX_OUTPUT_TOKENS,
}
DEEPSEEK_TEXT_BODY = {
    "thinking": {"type": "enabled"},
    "reasoning_effort": "low",
    "response_format": {"type": "text"},
    "max_tokens": MODEL_MAX_OUTPUT_TOKENS,
}


def model_providers(user):
    from model_billing import model_rates

    relay = user.get("model_route", "official") == "relay"
    model_rates(user)
    connection = user.get("relay_connection", {}) if relay else {}
    if relay and (not connection.get("base_url") or not connection.get("model")):
        raise ValueError("Relay connection is not verified")
    provider = {
        "id": "deepseek_companion",
        "provider": "openai",
        "type": "openai_chat_completion",
        "provider_type": "chat_completion",
        "enable": bool(load_key(relay=relay)),
        "key": [
            "$QQCOMPANION_RELAY_API_KEY" if relay else "$QQCOMPANION_DEEPSEEK_API_KEY"
        ],
        "api_base": connection.get("base_url", "https://api.deepseek.com/v1"),
        "model": connection.get("model", user["model"]),
        "timeout": MODEL_REQUEST_TIMEOUT,
        "proxy": "",
        "custom_headers": {},
        "custom_extra_body": copy.deepcopy(DEEPSEEK_BODY),
    }
    text_provider = copy.deepcopy(provider)
    text_provider.update(
        id="deepseek_companion_text",
        custom_extra_body=copy.deepcopy(DEEPSEEK_TEXT_BODY),
    )
    providers = [provider, text_provider]
    if relay:
        for source in (provider, text_provider):
            fallback = copy.deepcopy(source)
            fallback.update(
                id="deepseek_official_fallback"
                + ("_text" if source is text_provider else ""),
                enable=bool(load_key()),
                key=["$QQCOMPANION_DEEPSEEK_API_KEY"],
                api_base="https://api.deepseek.com/v1",
                model=user["model"],
            )
            providers.append(fallback)
    return providers


def prepare():
    sys.path.insert(0, str(ROOT / "astrbot"))
    os.environ["ASTRBOT_ROOT"] = str(ROOT / "astrbot")
    from astrbot.core.config.default import DEFAULT_CONFIG

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    user = read_settings()
    path = ROOT / "astrbot/data/cmd_config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
    cfg["timezone"] = "Asia/Shanghai"
    cfg["admins_id"] = [user["owner_qq"]] if user["owner_qq"] else []
    cfg["platform_settings"].update(
        enable_id_white_list=False,
        ignore_bot_self_message=True,
        no_permission_reply=False,
    )
    cfg["provider_settings"]["enable"] = False
    cfg["provider_sources"] = []
    cfg["provider"] = model_providers(user)
    cfg["agent_runner"]["config"]["model"]["request_max_retries"] = 0
    cfg["platform"] = [
        {
            "id": "qq_companion",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": 6199,
            "ws_reverse_token": local_token("onebot"),
        }
    ]
    cfg["dashboard"].update(host="127.0.0.1", port=6185)
    cfg["log_level"] = "INFO"
    atomic_json(path, cfg)
    source = ROOT / "plugins/astrbot_plugin_qq_companion"
    target = ROOT / "astrbot/data/plugins/astrbot_plugin_qq_companion"
    shutil.copytree(source, target, dirs_exist_ok=True)
    dist = ROOT / "astrbot/astrbot/dashboard/dist"
    if not dist.exists():
        shutil.copytree(ROOT / "astrbot/dashboard-release/dist", dist)
    print("Isolated configuration prepared; credentials were not printed.")


def prepare_napcat():
    user = read_settings()
    if user["bot_qq"]:
        onebot = {
            "network": {
                "httpServers": [],
                "httpSseServers": [],
                "httpClients": [],
                "websocketServers": [],
                "websocketClients": [
                    {
                        "name": "QQCompanion",
                        "enable": True,
                        "url": "ws://127.0.0.1:6199/ws",
                        "messagePostFormat": "array",
                        "reportSelfMessage": False,
                        "token": local_token("onebot"),
                        "debug": False,
                        "heartInterval": 30000,
                        "reconnectInterval": 5000,
                    }
                ],
            },
            "musicSignUrl": "",
            "enableLocalFile2Url": False,
            "parseMultMsg": False,
        }
        atomic_json(ROOT / f"napcat/config/onebot11_{user['bot_qq']}.json", onebot)
    print("NapCat account configuration prepared.")


if __name__ == "__main__":
    prepare()
    if "--framework-only" not in sys.argv:
        prepare_napcat()
