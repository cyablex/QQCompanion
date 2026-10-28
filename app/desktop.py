"""Launch the control window; its initial loading flow starts the companion."""

import ctypes
import os
import sys
import traceback
import tempfile

from settings import ROOT

TITLE = "大肥鱼控制台"


def main():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, "Local\\QQCompanionDesktop")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        user = ctypes.windll.user32
        user.FindWindowW.restype = ctypes.c_void_p
        window = user.FindWindowW(None, TITLE)
        if window:
            user.ShowWindow(ctypes.c_void_p(window), 9)
            user.SetForegroundWindow(ctypes.c_void_p(window))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return
    try:
        # Start loading the shared voice before importing the window framework.
        # The service honors game mode, pause and voice settings independently.
        from services import start

        start(["voice"])
        # This window plays the user's fixed local greeting after the opening.
        arguments = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
            arguments + " --autoplay-policy=no-user-gesture-required"
        ).strip()
        import webview
        from desktop_api import DesktopAPI

        index = ROOT / "frontend/dist/index.html"
        if not index.is_file():
            raise RuntimeError("桌面界面文件缺失，请重新构建 frontend。")
        api = DesktopAPI()
        window = webview.create_window(
            TITLE,
            str(index),
            js_api=api,
            width=1200,
            height=820,
            min_size=(960, 680),
            background_color="#F3F8FF",
            frameless=True,
            easy_drag=False,
            shadow=True,
            text_select=True,
        )
        api._attach_window(window)
        profiles = ROOT / "cache/desktop-webview"
        profiles.mkdir(parents=True, exist_ok=True)
        # A closing WebView2 child can still hold its profile lock. Each launch
        # owns a separate profile so rapid reopening cannot reuse that lock.
        session = tempfile.TemporaryDirectory(prefix='session-', dir=profiles, ignore_cleanup_errors=True)
        storage = session.name
        os.environ["TEMP"] = str(storage)
        os.environ["TMP"] = str(storage)
        webview.start(
            gui="edgechromium",
            http_server=True,
            private_mode=True,
            storage_path=str(storage),
        )
        session.cleanup()
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # pythonw has no terminal; retain the actual startup failure locally.
        log = ROOT / 'logs/desktop.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a', encoding='utf-8') as output:
            traceback.print_exc(file=output)
        detail = f"缺少模块：{exc.name}" if isinstance(exc, ModuleNotFoundError) else type(exc).__name__
        ctypes.windll.user32.MessageBoxW(
            None,
            f"控制台未能打开（{detail}）。\n详细原因已保存在 logs/desktop.log。",
            TITLE,
            0x10,
        )
        sys.exit(1)
