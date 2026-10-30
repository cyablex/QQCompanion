"""A readable launch page with a live, local QR image when needed."""

import html
import json
import time

from settings import ROOT


def load_state():
    if (ROOT / "config/offline-development.json").exists():
        return dict(phase="offline_development", qr_available=False)
    try:
        state = json.loads((ROOT / "data/startup.json").read_text(encoding="utf-8"))
        if not 0 <= time.time() - state.get("updated", 0) < 45:
            return dict(phase="status_unavailable")
        return state
    except (OSError, ValueError):
        return dict(phase="starting")


def render(state, csrf=""):
    phase = state.get("phase", "starting")
    title, detail = {
        "offline_development": (
            "大肥鱼正在离线开发",
            "QQ 已退出，自动登录与开机启动已暂停。恢复登录需要你另行确认，不会自动到期恢复。",
        ),
        "starting": ("正在启动", "正在启动后台服务，请稍候。页面会自动刷新。"),
        "connecting": (
            "QQ 正在连接机器人",
            "正在等待消息连接完成；此时还不能确认聊天已就绪。",
        ),
        "session_unavailable": (
            "QQ 登录会话异常，暂时不能聊天",
            "QQ 虽然报已登录，但好友列表与网络状态尚未恢复。后台会检查并有限次重连；这不代表已经上线。",
        ),
        "recovery_failed": (
            "QQ 会话自动恢复失败",
            "本小时已尝试两次恢复，已停止反复重启。请打开 QQ 登录管理检查登录或手机验证。",
        ),
        "status_unavailable": (
            "启动状态检测已中断",
            "后台状态已超过45秒没有更新，当前不能确认QQ上线。重新启动程序可恢复检测；若持续出现，需要检查后台服务。",
        ),
        "ready": (
            "大肥鱼已就绪",
            "QQ 已登录，机器人消息连接正常。可以直接去 QQ 聊天。",
        ),
        "waiting_login": (
            "需要你确认 QQ 登录",
            "请用手机上的大肥鱼小号扫描下方二维码，并在手机上确认。",
        ),
        "reconnecting": (
            "QQ 正在重连",
            "正在等待 QQ 恢复连接；若需要扫码，二维码会显示在这里。",
        ),
        "wrong_account": (
            "当前登录的 QQ 账号不匹配",
            "请打开 QQ 登录管理，切换到设置中的大肥鱼小号。",
        ),
        "paused": ("QQ 已登录，聊天已暂停", "在主号发送 /恢复 可以恢复聊天。"),
    }.get(phase, ("正在检查", "正在检查账号和消息连接状态。"))
    if state.get("login_phase") == "qrcode_scanned":
        detail = "二维码已扫描，请在手机上确认授权；确认完成后页面会自动显示连接结果。"
    image = (
        '<img src="/qq-qr.png" alt="QQ 登录二维码" width="300" height="300">'
        if phase == "waiting_login" and state.get("qr_available")
        else ""
    )
    if phase == "waiting_login" and state.get("login_phase") != "qrcode_scanned":
        image = ('<form method="post" action="/qq-qr/refresh" style="text-align:right">'
                 f'<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">'
                 '<button type="submit" aria-label="刷新登录二维码" title="重新获取二维码" '
                 'style="font-size:26px;cursor:pointer;border:1px solid #cdd7d4;border-radius:50%;background:white;width:42px;height:42px">↻</button></form>') + image
    if phase == "offline_development":
        return f'<!doctype html><meta charset="utf-8"><title>大肥鱼 · 离线开发</title><main style="font:18px/1.8 system-ui;max-width:650px;margin:60px auto"><h1>{title}</h1><p>{detail}</p><a href="/">本机设置</a></main>'
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5"><title>大肥鱼 · 启动状态</title><style>body{{font:17px/1.7 system-ui;background:#f5f4ef;color:#243339;max-width:650px;margin:60px auto;padding:20px}}main{{background:white;padding:30px;border-radius:18px}}img{{display:block;max-width:100%;height:auto;margin:24px auto}}a{{color:#226857}}.muted{{color:#617078;font-size:14px}}</style><main><h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>{image}<p><a href="/qq-login">打开 QQ 登录管理</a> · <a href="/">本机设置</a></p><p class="muted">本页每 5 秒刷新。需要手机验证时无法跳过授权；扫码完成后无需再次点击启动。登录成功后可以关闭本页。</p></main></html>"""
