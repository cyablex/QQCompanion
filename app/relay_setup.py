"""Collect relay connection details locally without activating a provider."""

import html
import json
from urllib.parse import urlsplit

from settings import ROOT, atomic_json, read_settings, save_key


def read_relay():
    cfg = dict(base_url="https://zrocode.cc/v1", model="deepseek-v4.1-flash")
    path = ROOT / "config/relay.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def render(csrf, daily_cny):
    cfg = read_relay()
    active = read_settings().get("model_route", "official") == "relay"
    stage = (
        "当前已接入中转：聊天、思考、看图与后台整理使用中转模型；中转连接失败或明确拦截时，在预算允许下本轮自动转官方，后续新问题继续优先中转。联网搜索和正文抓取使用原官方 Key。保存资料不会立即更改运行中的连接，接入新地址或模型需重新核实并重启。"
        if active
        else "这里先保存接入资料。当前仍使用官方模型；保存不会切换服务、调用模型或发送消息。后续完成兼容性核实与测试后再接入。"
    )

    def esc(value):
        return html.escape(str(value), quote=True)

    saved = (
        "已保存；留空保留现有中转 Key"
        if (ROOT / "config/relay-api-key.dpapi").exists()
        else "粘贴中转站的专用 Key"
    )
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>大肥鱼 · 中转接入设置</title>
<style>body{{font:16px/1.7 system-ui;background:#f5f4ef;color:#243339;max-width:720px;margin:36px auto;padding:0 20px}}main{{background:white;padding:28px;border-radius:18px}}label{{display:block;font-weight:600;margin-top:20px}}input{{box-sizing:border-box;width:100%;font:inherit;padding:12px;border:1px solid #cbd3d5;border-radius:8px}}button{{background:#226857;color:white;font:inherit;padding:12px 20px;border:0;border-radius:8px;margin-top:24px}}small{{color:#617078}}.notice{{background:#edf5f1;padding:14px;border-radius:10px}}</style>
<h1>中转接入设置</h1><main><p class="notice">{stage}</p>
<p>日常使用预算：合计 {daily_cny:g} 元/天。官方工具与中转模型共用这份日常预算；验收测试单独记账。{"当前中转组实测人民币计价：每百万输入 0.6 元、缓存输入 0.012 元、输出 2.4 元。" if active else "中转实际计价待核实。"}</p>
<form method="post" action="/relay/save" autocomplete="off"><input type="hidden" name="csrf" value="{esc(csrf)}">
<label for="base_url">API 地址</label><input id="base_url" name="base_url" type="url" required value="{esc(cfg["base_url"])}"><small>已填好 zrocode 的 API 地址；不是登录页或充值页。</small>
<label for="relay_key">中转 API Key</label><input id="relay_key" name="relay_key" type="password" autocomplete="new-password" placeholder="{saved}" maxlength="512"><small>只填中转站生成的专用 Key，不要发到聊天。使用 Windows 当前用户加密保存，不覆盖原有官方 Key；已有 Key 时留空保留。</small>
<label for="model">模型名</label><input id="model" name="model" required maxlength="128" value="{esc(cfg["model"])}"><small>先按你选的 deepseek-v4.1-flash 填写，是否支持及实际版本待测试。</small>
<button type="submit">保存接入资料</button></form><p><a href="/">返回本机设置</a></p></main></html>'''


def save(data):
    base_url = data.get("base_url", [""])[0].strip().rstrip("/")
    model = data.get("model", [""])[0].strip()
    key = data.get("relay_key", [""])[0].strip()
    try:
        parsed = urlsplit(base_url)
        valid_url = (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.port != 0
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and len(base_url) <= 2048
            and not any(c.isspace() or ord(c) < 32 for c in base_url)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("请填写有效的 HTTPS API 地址，不带账号密码或查询参数。")
    if not model or len(model) > 128 or any(c.isspace() or ord(c) < 32 for c in model):
        raise ValueError("请填写有效的模型名。")
    if key and (
        not 12 <= len(key) <= 512 or any(c.isspace() or ord(c) < 32 for c in key)
    ):
        raise ValueError("中转 API Key 格式无效。")
    if not key and not (ROOT / "config/relay-api-key.dpapi").exists():
        raise ValueError("首次保存请填写中转 API Key。")
    if key:
        save_key(key, relay=True)
    # Runtime uses the separately verified activation snapshot in user settings.
    atomic_json(ROOT / "config/relay.json", dict(base_url=base_url, model=model))
