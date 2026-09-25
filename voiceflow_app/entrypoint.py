"""Application startup and packaged-runtime self-test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from .config import (
    APP_DIR,
    APP_NAME,
    LAST_RUN_DIR,
    LOG_DIR,
    RUN_LOG_DIR,
    START_MINIMIZED,
)
from .single_instance import (
    acquire_single_instance_lock,
    single_instance_last_error,
    single_instance_mutex_name,
)


_BOOTSTRAP_STARTED_AT = time.monotonic()


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

    checks: dict[str, bool] = {
        "problem_log_dir_name": LOG_DIR.name == "Логи проблем",
    }
    errors: dict[str, str] = {}

    dependency_names = (
        "numpy",
        "sounddevice",
        "faster_whisper",
        "pyperclip",
        "pyautogui",
        "keyboard",
        "pystray",
        "pillow",
    )
    try:
        from .dependencies import (
            Image,
            ImageDraw,
            WhisperModel,
            keyboard,
            np,
            pyautogui,
            pyperclip,
            pystray,
            sd,
        )
        checks.update({
            "numpy": np is not None,
            "sounddevice": sd is not None,
            "faster_whisper": WhisperModel is not None,
            "pyperclip": pyperclip is not None,
            "pyautogui": pyautogui is not None,
            "keyboard": keyboard is not None,
            "pystray": pystray is not None,
            "pillow": Image is not None and ImageDraw is not None,
        })
    except BaseException as exc:
        for name in dependency_names:
            checks[name] = False
        errors["runtime_dependencies"] = f"{type(exc).__name__}: {exc}"

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

    try:
        from .app.main_window import VoiceFlowOfflineApp  # noqa: F401
        checks["app_import"] = True
    except BaseException as exc:
        checks["app_import"] = False
        errors["app_import"] = f"{type(exc).__name__}: {exc}"

    try:
        from .composition import build_default_application_services
        composition = build_default_application_services()
        checks["application_composition"] = all(
            item is not None
            for item in (
                composition.recorder,
                composition.transcriber,
                composition.cleaner,
                composition.notification_factory,
                composition.tray_factory,
                composition.text_inserter,
                composition.voice_action_executor,
            )
        )
    except BaseException as exc:
        checks["application_composition"] = False
        errors["application_composition"] = f"{type(exc).__name__}: {exc}"

    payload = {
        "app": APP_NAME,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "app_dir": str(APP_DIR),
        "log_dir": str(LOG_DIR),
        "checks": checks,
        "errors": errors,
    }
    _emit_self_test_payload(payload)
    return 0 if all(checks.values()) else 1


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(run_self_test())

    # Acquire the mutex before diagnostics, third-party dependencies, Tk or
    # application imports. A blocked second instance must not reset _last_run
    # or pay the heavy import cost before it exits.
    if not acquire_single_instance_lock():
        try:
            print(
                f"{APP_NAME} уже запущен. Второй экземпляр закрыт, "
                "чтобы не конфликтовали горячие клавиши."
            )
        except Exception:
            pass
        return
    lock_acquired_at = time.monotonic()

    from .diagnostics import (
        install_exception_logging,
        log_exception,
        log_info,
        log_warning,
        write_diagnostics_snapshot,
    )

    install_exception_logging()
    lock_error = single_instance_last_error()
    if lock_error:
        log_warning(
            "Could not acquire single-instance lock; continuing",
            error=lock_error,
            mutex_name=single_instance_mutex_name(),
        )
    else:
        log_info(
            "Single-instance lock acquired before heavy imports",
            mutex_name=single_instance_mutex_name(),
            bootstrap_to_lock_ms=round(
                (lock_acquired_at - _BOOTSTRAP_STARTED_AT) * 1000.0,
                1,
            ),
        )

    try:
        import tkinter as tk
        from tkinter import ttk
        from .app.main_window import VoiceFlowOfflineApp
    except BaseException as exc:
        log_exception("Application startup import failed", exc)
        raise

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
