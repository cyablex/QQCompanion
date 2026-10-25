"""Loopback-only setup form. It never returns or logs API credentials."""

import html
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from settings import ROOT, atomic_json, local_token, read_settings, save_key

PORT = 8765
CSRF = local_token("setup-csrf")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def allowed_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def response(self, body, status=200, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        if not self.allowed_host():
            return self.response("Forbidden", 403)
        cfg = read_settings()
        if self.path == "/relay":
            from relay_setup import render

            return self.response(render(CSRF, float(cfg["daily_cny"])))
        health = {}
        try:
            health = json.loads((ROOT / "data/health.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if self.path in {"/startup", "/qq-qr.png"}:
            from startup_page import render, load_state

            state = load_state()
            if self.path == "/qq-qr.png":
                image = ROOT / "napcat/cache/qrcode.png"
                if (
                    state.get("phase") != "waiting_login"
                    or not state.get("qr_available")
                    or not image.is_file()
                ):
                    return self.response("二维码尚未准备好，请稍候。", 404)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.end_headers()
                self.wfile.write(image.read_bytes())
                return
            return self.response(render(state))
        if self.path == "/qq-login":
            path = ROOT / "napcat/config/webui.json"
            if not path.exists():
                return self.response("QQ 登录服务尚未启动。", 503)
            from urllib.parse import urlencode

            config = json.loads(path.read_text(encoding="utf-8"))
            url = "http://127.0.0.1:6099/webui?" + urlencode({"token": config["token"]})
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            return
        if self.path == "/status":
            body = {
                "configured": bool(
                    cfg["owner_qq"]
                    and cfg["bot_qq"]
                    and (ROOT / "config/api-key.dpapi").exists()
                ),
                "key_saved": (ROOT / "config/api-key.dpapi").exists(),
                "live": cfg["live"],
                "persona_name": cfg["name"],
                "owner_title": cfg["owner_title"],
                "paused": cfg["paused"],
                "proactive": cfg["proactive"],
                "guests": cfg["guests"],
                "friend_accept": cfg["friend_accept"],
                "qzone": cfg["qzone"],
                "image_understanding": cfg["image_understanding"],
                "voice_understanding": cfg["voice_understanding"],
                "game_mode": cfg.get("game_mode", False),
                "model_route": cfg.get("model_route", "official"),
                "daily_cny": cfg["daily_cny"],
                "send_memes": cfg["send_memes"],
                "autonomous_posts": cfg["autonomous_posts"],
                "guest_mode": cfg["guest_mode"],
                "health": health,
            }
            return self.response(
                json.dumps(body, ensure_ascii=False),
                content_type="application/json; charset=utf-8",
            )
        if self.path != "/":
            return self.response("Not found", 404)

        def esc(value):
            return html.escape(str(value), quote=True)

        saved = (
            "已保存；留空保留现有 Key"
            if (ROOT / "config/api-key.dpapi").exists()
            else "尚未填写"
        )
        mode = cfg.get("guest_mode", "allowlist")
        mode_options = (
            '<option value="allowlist"'
            + (" selected" if mode == "allowlist" else "")
            + ">仅已登记的测试好友</option>"
            '<option value="open"'
            + (" selected" if mode == "open" else "")
            + ">开放好友申请和好友私聊</option>"
        )
        stage = (
            "QQ 已连接"
            if cfg["live"]
            and not cfg["paused"]
            and health.get("qq_connected")
            and time.time() - health.get("updated", 0) < 90
            else "已暂停"
            if cfg["paused"]
            else "等待 QQ 登录或连接"
        )
        online = "已连接" if health.get("qq_connected") else "连接检查中或已断开"
        page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>QQ AI 小号 · 本机设置</title>
<style>body{{background:#f5f4ef;color:#243339;font:16px/1.6 system-ui;max-width:740px;margin:36px auto;padding:0 20px}}h1{{font-size:28px}}main{{background:white;border-radius:18px;padding:28px;box-shadow:0 8px 32px #0001}}label{{display:block;font-weight:600;margin-top:18px}}input,textarea{{box-sizing:border-box;width:100%;padding:11px;border:1px solid #cbd3d5;border-radius:8px;font:inherit}}textarea{{min-height:100px}}button{{margin-top:24px;background:#226857;color:white;padding:12px 24px;border:0;border-radius:8px;font:inherit;cursor:pointer}}small,.muted{{color:#617078}}.notice{{background:#edf5f1;padding:12px 16px;border-radius:10px}}code{{font-size:14px}}</style>
<h1>QQ AI 小号 · 本机设置</h1><main><p class="notice">状态：{stage} · QQ {online}。<br>保存只写入本机，不会主动发消息。<a href="/qq-login">小号登录管理</a> · <a href="/status">查看运行状态</a></p><p class="notice"><a href="/relay">填写中转 API 地址与 Key →</a><br>中转资料独立保存，原有官方 Key 保留。日常合计预算 {float(cfg['daily_cny']):g} 元/天。</p>
<form method="post" action="/save" autocomplete="off"><input type="hidden" name="csrf" value="{CSRF}">
<label>你的主号 QQ（数字账号）</label><input name="owner_qq" inputmode="numeric" required pattern="[1-9][0-9]{{4,19}}" value="{esc(cfg["owner_qq"])}"><small>这是管理员账号，用于识别你；不要填昵称。</small>
<label>AI 小号 QQ（数字账号）</label><input name="bot_qq" inputmode="numeric" required pattern="[1-9][0-9]{{4,19}}" value="{esc(cfg["bot_qq"])}"><small>必须和主号不同；稍后由你扫码登录这个小号。</small>
<label>DeepSeek 官方 API Key</label><input name="api_key" type="password" autocomplete="new-password" placeholder="{saved}"><small>使用 Windows 当前用户加密存储。不会显示回页面，也不需要发到聊天。</small>
<label>模型名</label><input name="model" value="{esc(cfg["model"])}" required>
<label>小号名字（可随时改）</label><input name="name" value="{esc(cfg["name"])}" maxlength="40" required>
<label>对你的称呼</label><input name="owner_title" value="{esc(cfg["owner_title"])}" maxlength="40" required>
<label>性格与说话方式（可随时改）</label><textarea name="persona" maxlength="4000">{esc(cfg["persona"])}</textarea>
<label>测试好友 QQ（可选，多人用英文逗号分隔）</label><input name="guest_ids" value="{esc(",".join(cfg.get("guest_ids", [])))}"><small>先登记测试对象，不会仅因保存而开放其他好友或自动接受申请。</small>
<label>好友范围</label><select name="guest_mode" style="font:inherit;padding:10px;width:100%">{mode_options}</select><small>开放模式不限制每天接受好友的人数。普通好友每天 {cfg.get("guest_daily_messages", 50)} 条；指定好友使用各自设置的额度；全号聊天回复每天共 {cfg.get("daily_chat_messages", 500)} 条，费用上限另行有效。其他开关保持原设置。</small>
<label>游戏模式</label><select name="game_mode" style="font:inherit;padding:10px;width:100%"><option value="off"{' selected' if not cfg.get('game_mode') else ''}>关闭：语音按需处理</option><option value="on"{' selected' if cfg.get('game_mode') else ''}>开启：关闭本地语音推理</option></select><small>开启后暂停本地语音识别和合成，文字聊天继续；关闭后按需使用，不自动常驻启动合成服务。</small>
<button type="submit">保存到本机</button></form><p class="muted">人设下次回复时生效，保留已有聊天记忆。模型名或 Key 改动后需重启机器人。主人可在 QQ 发 /状态、/帮助、/午休 120、/暂停、/恢复、/游戏模式 开、/游戏模式 关。</p></main></html>'''
        self.response(page)

    def do_POST(self):
        if not self.allowed_host() or self.path not in {"/save", "/relay/save"}:
            return self.response("Forbidden", 403)
        # Embedded browsers can report an opaque or application origin. The
        # unpredictable form token below is the CSRF check; no CORS is enabled.
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type != "application/x-www-form-urlencoded":
            return self.response("Invalid form content type", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 32768:
                return self.response("Invalid request", 400)
            data = parse_qs(
                self.rfile.read(length).decode("utf-8"), keep_blank_values=True
            )
            if not secrets.compare_digest(data.get("csrf", [""])[0], CSRF):
                return self.response("请重新打开设置页后保存。", 403)
            if self.path == "/relay/save":
                from relay_setup import save

                try:
                    save(data)
                except ValueError as exc:
                    return self.response(html.escape(str(exc)) + '<p><a href="/relay">返回中转设置</a></p>', 400)
                return self.response('<!doctype html><meta charset="utf-8"><title>资料已保存</title><h1>中转接入资料已保存</h1><p>Key 已加密存储，原有官方 Key 保留。运行中的模型未因本次保存改变，也未发起收费测试；新接入资料需核实并重启后生效。</p><p><a href="/relay">返回中转设置</a> · <a href="/">本机设置</a></p>')
            values = {
                k: data.get(k, [""])[0].strip()
                for k in [
                    "owner_qq",
                    "bot_qq",
                    "name",
                    "owner_title",
                    "persona",
                    "model",
                ]
            }
            for field in ["owner_qq", "bot_qq"]:
                v = values[field]
                if (
                    not v.isascii()
                    or not v.isdigit()
                    or not 5 <= len(v) <= 20
                    or v.startswith("0")
                ):
                    return self.response(
                        "QQ 号应填写 5–20 位数字账号。<a href='/'>返回</a>", 400
                    )
            if values["owner_qq"] == values["bot_qq"]:
                return self.response("主号和小号需要不同。<a href='/'>返回</a>", 400)
            if (
                not values["name"]
                or len(values["name"]) > 40
                or len(values["persona"]) > 4000
            ):
                return self.response("名字或人设长度无效。<a href='/'>返回</a>", 400)
            key = data.get("api_key", [""])[0].strip()
            if key and (
                len(key) < 12 or len(key) > 512 or any(c.isspace() for c in key)
            ):
                return self.response("API Key 格式无效。<a href='/'>返回</a>", 400)
            if key:
                save_key(key)
            cfg = read_settings()
            if cfg["owner_qq"] and (
                cfg["owner_qq"] != values["owner_qq"]
                or cfg["bot_qq"] != values["bot_qq"]
            ):
                cfg.update(
                    live=False,
                    proactive=False,
                    guests=False,
                    friend_accept=False,
                    qzone=False,
                )
            cfg.update(values)
            guests = [
                x.strip()
                for x in data.get("guest_ids", [""])[0].split(",")
                if x.strip()
            ]
            if (
                any(
                    not x.isascii() or not x.isdigit() or not 5 <= len(x) <= 20
                    for x in guests
                )
                or len(guests) > 10
            ):
                return self.response('测试好友 QQ 格式无效。<a href="/">返回</a>', 400)
            cfg["guest_ids"] = list(dict.fromkeys(guests))
            mode = data.get("guest_mode", [cfg.get("guest_mode", "allowlist")])[0]
            if mode not in {"open", "allowlist"}:
                return self.response("好友范围无效。<a href='/'>返回</a>", 400)
            cfg["guest_mode"] = mode
            if mode == "open":
                cfg.update(guests=True, friend_accept=True)
            if "game_mode" in data:
                game_mode = data["game_mode"][0]
                if game_mode not in {"on", "off"}:
                    return self.response("游戏模式选项无效。<a href='/'>返回</a>", 400)
                cfg["game_mode"] = game_mode == "on"
            cfg["revision"] += 1
            atomic_json(ROOT / "config/user.json", cfg)
            if cfg.get("game_mode"):
                from game_mode import close_local_voice
                stopped = close_local_voice()
                if stopped["denied"]:
                    return self.response("游戏模式已保存，但部分语音进程未能关闭，请在任务管理器核对。<a href='/'>返回</a>")
            self.response(
                "<!doctype html><meta charset='utf-8'><title>保存完成</title><body style='font:18px system-ui;max-width:680px;margin:70px auto'><h1>已保存到本机</h1><p>可以返回 QQ 继续聊天。API Key 不会显示；保存设置没有发送任何消息。</p><p>模型名或 Key 改动需要重启机器人；人设下次生成时生效。</p><a href='/'>返回设置</a> · <a href='/status'>查看运行状态</a></body>"
            )
        except Exception:
            self.response("保存未完成，请检查本机设置服务。", 500)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
