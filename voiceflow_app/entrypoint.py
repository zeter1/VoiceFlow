"""Application startup and packaged-runtime self-test."""

from __future__ import annotations

import json
import sys
import tkinter as tk
from tkinter import ttk

from .config import (
    APP_DIR,
    APP_NAME,
    LAST_RUN_DIR,
    LOG_DIR,
    RUN_LOG_DIR,
    START_MINIMIZED,
)
from .dependencies import Image, ImageDraw, WhisperModel, keyboard, np, pyautogui, pyperclip, pystray, sd
from .diagnostics import (
    acquire_single_instance_lock,
    install_exception_logging,
    log_info,
    write_diagnostics_snapshot,
)
from .app.main_window import VoiceFlowOfflineApp


def run_self_test() -> int:
    """Lightweight packaged-runtime check without opening the GUI or microphone."""
    checks = {
        "numpy": np is not None,
        "sounddevice": sd is not None,
        "faster_whisper": WhisperModel is not None,
        "pyperclip": pyperclip is not None,
        "pyautogui": pyautogui is not None,
        "keyboard": keyboard is not None,
        "pystray": pystray is not None,
        "pillow": Image is not None and ImageDraw is not None,
    }
    try:
        import language_tool_python  # type: ignore  # noqa: F401
        checks["language_tool_python"] = True
    except Exception:
        checks["language_tool_python"] = False

    payload = {
        "app": APP_NAME,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "app_dir": str(APP_DIR),
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if all(checks.values()) else 1

def main() -> None:
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(run_self_test())

    install_exception_logging()
    if not acquire_single_instance_lock():
        try:
            print(f"{APP_NAME} уже запущен. Второй экземпляр закрыт, чтобы не конфликтовали горячие клавиши.")
        except Exception:
            pass
        return
    log_info("Application starting", argv=sys.argv, app_dir=APP_DIR, log_dir=LOG_DIR, run_log_dir=RUN_LOG_DIR, last_run_dir=LAST_RUN_DIR)
    write_diagnostics_snapshot()
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    app = VoiceFlowOfflineApp(root)
    if START_MINIMIZED:
        app.hide_main_window(show_notification=False)
    root.mainloop()
    log_info("Application mainloop exited")
