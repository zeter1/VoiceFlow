"""Application startup and packaged-runtime self-test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

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


def _emit_self_test_payload(payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    output_path = os.getenv("VOICEFLOW_SELF_TEST_OUTPUT", "").strip()
    if output_path:
        try:
            Path(output_path).write_text(encoded + "\n", encoding="utf-8")
        except Exception:
            pass
    try:
        print(encoded)
    except Exception:
        pass


def run_self_test() -> int:
    """Check packaged imports without opening the GUI or microphone."""
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
    errors: dict[str, str] = {}

    try:
        import language_tool_python  # type: ignore  # noqa: F401
        checks["language_tool_python"] = True
    except Exception as exc:
        checks["language_tool_python"] = False
        errors["language_tool_python"] = f"{type(exc).__name__}: {exc}"

    try:
        import tkinter  # noqa: F401
        checks["tkinter"] = True
    except Exception as exc:
        checks["tkinter"] = False
        errors["tkinter"] = f"{type(exc).__name__}: {exc}"

    # Import the full application graph inside the guarded self-test so a
    # packaged import regression becomes structured evidence instead of a
    # windowed error dialog that leaves CI waiting forever.
    try:
        from .app.main_window import VoiceFlowOfflineApp  # noqa: F401
        checks["app_import"] = True
    except BaseException as exc:
        checks["app_import"] = False
        errors["app_import"] = f"{type(exc).__name__}: {exc}"

    payload = {
        "app": APP_NAME,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "app_dir": str(APP_DIR),
        "checks": checks,
        "errors": errors,
    }
    _emit_self_test_payload(payload)
    return 0 if all(checks.values()) else 1


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(run_self_test())

    # GUI imports stay after the self-test gate. This is important for
    # diagnostics of windowed packaged builds.
    import tkinter as tk
    from tkinter import ttk
    from .app.main_window import VoiceFlowOfflineApp

    install_exception_logging()
    if not acquire_single_instance_lock():
        try:
            print(f"{APP_NAME} уже запущен. Второй экземпляр закрыт, чтобы не конфликтовали горячие клавиши.")
        except Exception:
            pass
        return
    log_info(
        "Application starting",
        argv=sys.argv,
        app_dir=APP_DIR,
        log_dir=LOG_DIR,
        run_log_dir=RUN_LOG_DIR,
        last_run_dir=LAST_RUN_DIR,
    )
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
