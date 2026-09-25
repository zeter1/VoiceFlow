"""Structured logging, runtime diagnostics and process-level failure handling."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import logging
import os
import platform
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .single_instance import acquire_single_instance_lock
from .config import (
    APP_DIR,
    APP_LOG_PATH,
    CATEGORY_LOG_PATHS,
    CRASH_LOG_PATH,
    CUDA_REQUIRED_WINDOWS_DLLS,
    DIAGNOSTICS_LOG_PATH,
    DICTATION_TEXT_LOG_PATH,
    IS_WINDOWS,
    LAST_APP_LOG_PATH,
    LAST_CATEGORY_LOG_PATHS,
    LAST_CRASH_LOG_PATH,
    LAST_DIAGNOSTICS_LOG_PATH,
    LAST_DICTATION_TEXT_LOG_PATH,
    LAST_RUN_DIR,
    LAST_RUN_POINTER_PATH,
    LEGACY_SETTINGS_PATH,
    LOG_DIR,
    MAX_RUN_LOG_DIRS,
    RUN_LOG_DIR,
    SETTINGS_DIR,
    SETTINGS_PATH,
)

_LOGGING_READY = False
_DICTATION_LOG_LOCK = threading.Lock()
_CATEGORY_LOG_LOCK = threading.Lock()
def _json_default(value: object) -> str:
    try:
        return str(value)
    except Exception:
        return "<unprintable>"


def _format_log_context(context: dict[str, object]) -> str:
    if not context:
        return ""
    try:
        return " | " + json.dumps(context, ensure_ascii=False, default=_json_default, sort_keys=True)
    except Exception:
        return f" | context={context!r}"


def _cleanup_old_run_logs() -> None:
    """Keep recent per-launch log folders and remove very old ones."""
    try:
        if not LOG_DIR.exists():
            return
        run_dirs = [path for path in LOG_DIR.glob("run_*") if path.is_dir()]
        run_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for old_dir in run_dirs[MAX_RUN_LOG_DIRS:]:
            try:
                shutil.rmtree(old_dir)
            except Exception as exc:
                log_exception("Could not remove old run log folder", exc, log_dir=old_dir)
    except Exception:
        pass


def _reset_last_run_logs() -> None:
    """Prepare small overwriteable copies of the current run logs."""
    LAST_RUN_DIR.mkdir(parents=True, exist_ok=True)
    for path in (
        LAST_APP_LOG_PATH,
        LAST_CRASH_LOG_PATH,
        LAST_DICTATION_TEXT_LOG_PATH,
        *LAST_CATEGORY_LOG_PATHS.values(),
    ):
        try:
            path.write_text("", encoding="utf-8")
        except Exception:
            pass
    try:
        LAST_RUN_POINTER_PATH.write_text(str(RUN_LOG_DIR), encoding="utf-8")
    except Exception:
        pass


def configure_logging() -> None:
    """Configure one separate log set for each program launch."""
    global _LOGGING_READY
    if _LOGGING_READY:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _reset_last_run_logs()
        DICTATION_TEXT_LOG_PATH.write_text("", encoding="utf-8")
        _cleanup_old_run_logs()
        formatter = logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        logger = logging.getLogger("voiceflow")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.handlers.clear()

        app_handler = logging.FileHandler(APP_LOG_PATH, mode="a", encoding="utf-8")
        app_handler.setLevel(logging.DEBUG)
        app_handler.setFormatter(formatter)

        last_app_handler = logging.FileHandler(LAST_APP_LOG_PATH, mode="a", encoding="utf-8")
        last_app_handler.setLevel(logging.DEBUG)
        last_app_handler.setFormatter(formatter)

        crash_handler = logging.FileHandler(CRASH_LOG_PATH, mode="a", encoding="utf-8")
        crash_handler.setLevel(logging.ERROR)
        crash_handler.setFormatter(formatter)

        last_crash_handler = logging.FileHandler(LAST_CRASH_LOG_PATH, mode="a", encoding="utf-8")
        last_crash_handler.setLevel(logging.ERROR)
        last_crash_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)

        logger.addHandler(app_handler)
        logger.addHandler(last_app_handler)
        logger.addHandler(crash_handler)
        logger.addHandler(last_crash_handler)
        logger.addHandler(console_handler)
        logging.captureWarnings(True)
        _LOGGING_READY = True
        logger.info("Logging initialized%s", _format_log_context({
            "log_dir": LOG_DIR,
            "run_log_dir": RUN_LOG_DIR,
            "last_run_dir": LAST_RUN_DIR,
            "app_log": APP_LOG_PATH,
            "last_app_log": LAST_APP_LOG_PATH,
            "crash_log": CRASH_LOG_PATH,
            "last_crash_log": LAST_CRASH_LOG_PATH,
            "dictation_text_log": DICTATION_TEXT_LOG_PATH,
            "last_dictation_text_log": LAST_DICTATION_TEXT_LOG_PATH,
            "category_logs": CATEGORY_LOG_PATHS,
            "last_category_logs": LAST_CATEGORY_LOG_PATHS,
        }))
    except Exception as exc:
        try:
            print(f"Could not initialize logging: {exc}", file=sys.stderr)
        except Exception:
            pass


def log_event(level: int, message: str, **context: object) -> None:
    configure_logging()
    logging.getLogger("voiceflow").log(level, message + _format_log_context(context), stacklevel=3)


def log_info(message: str, **context: object) -> None:
    log_event(logging.INFO, message, **context)


def log_warning(message: str, **context: object) -> None:
    log_event(logging.WARNING, message, **context)


def log_exception(message: str, exc: object, **context: object) -> None:
    configure_logging()
    logger = logging.getLogger("voiceflow")
    if isinstance(exc, BaseException):
        context.setdefault("exception_type", type(exc).__name__)
        context.setdefault("exception", str(exc))
        logger.error(message + _format_log_context(context), exc_info=(type(exc), exc, exc.__traceback__), stacklevel=2)
    else:
        context.setdefault("exception", repr(exc))
        logger.error(message + _format_log_context(context), stacklevel=2)


def log_dictation_text(event: str, **context: object) -> None:
    """Append recognized text to this launch's dictation log and _last_run copy."""
    configure_logging()
    try:
        RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        LAST_RUN_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            **context,
        }
        line = json.dumps(record, ensure_ascii=False, default=_json_default, sort_keys=True) + "\n"
        with _DICTATION_LOG_LOCK:
            for path in (DICTATION_TEXT_LOG_PATH, LAST_DICTATION_TEXT_LOG_PATH):
                with path.open("a", encoding="utf-8") as file:
                    file.write(line)
    except Exception as exc:
        log_exception("Could not write dictation text log", exc, event=event)


def log_category(category: str, event: str, **context: object) -> None:
    """Write focused JSONL diagnostics for AI-assisted bug fixing."""
    configure_logging()
    category = category if category in CATEGORY_LOG_PATHS else "recording"
    try:
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "monotonic": round(time.monotonic(), 6),
            "category": category,
            "event": event,
            **context,
        }
        line = json.dumps(record, ensure_ascii=False, default=_json_default, sort_keys=True) + "\n"
        with _CATEGORY_LOG_LOCK:
            for path in (CATEGORY_LOG_PATHS[category], LAST_CATEGORY_LOG_PATHS[category]):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as file:
                    file.write(line)
    except Exception as exc:
        log_exception("Could not write category log", exc, category=category, event=event)


def dependency_status() -> dict[str, dict[str, object]]:
    modules = {
        "numpy": "numpy",
        "sounddevice": "sounddevice",
        "faster_whisper": "faster-whisper",
        "pyperclip": "pyperclip",
        "pyautogui": "PyAutoGUI",
        "keyboard": "keyboard",
        "language_tool_python": "language-tool-python",
        "pystray": "pystray",
        "PIL": "Pillow",
    }
    result: dict[str, dict[str, object]] = {}
    for module, package in modules.items():
        installed = False
        version = None
        try:
            installed = importlib.util.find_spec(module) is not None
            if installed:
                try:
                    version = importlib.metadata.version(package)
                except Exception:
                    version = "unknown"
        except Exception:
            installed = False
        result[module] = {"installed": installed, "version": version}
    if IS_WINDOWS:
        dll_paths = {dll_name: find_windows_dll(dll_name) for dll_name in CUDA_REQUIRED_WINDOWS_DLLS}
        result["cuda_runtime"] = {
            "installed": all(bool(path) for path in dll_paths.values()),
            "version": "CUDA 12.x + cuDNN 9 required by faster-whisper",
            "required_dlls": list(CUDA_REQUIRED_WINDOWS_DLLS),
            "dll_paths": dll_paths,
            "missing": [dll_name for dll_name, path in dll_paths.items() if not path],
        }
    return result


def windows_dll_search_dirs() -> list[Path]:
    search_dirs = [APP_DIR, Path(sys.executable).resolve().parent]
    search_dirs.extend(Path(part) for part in os.environ.get("PATH", "").split(os.pathsep) if part)
    return search_dirs


def find_windows_dll(dll_name: str) -> Optional[str]:
    if not IS_WINDOWS:
        return None
    for directory in windows_dll_search_dirs():
        try:
            path = directory / dll_name
            if path.exists():
                return str(path)
        except Exception:
            continue
    return None

def write_diagnostics_snapshot(extra: Optional[dict[str, object]] = None) -> None:
    """Write current runtime details useful for later debugging."""
    configure_logging()
    try:
        snapshot = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "app_dir": str(APP_DIR),
            "script": str(Path(sys.argv[0]).resolve()) if sys.argv else "",
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
            "argv": sys.argv,
            "platform": platform.platform(),
            "python_version": sys.version,
            "is_windows": sys.platform.startswith("win"),
            "dependencies": dependency_status(),
            "log_dir": str(LOG_DIR),
            "run_log_dir": str(RUN_LOG_DIR),
            "last_run_dir": str(LAST_RUN_DIR),
            "voiceflow_log": str(APP_LOG_PATH),
            "last_voiceflow_log": str(LAST_APP_LOG_PATH),
            "dictation_text_log": str(DICTATION_TEXT_LOG_PATH),
            "last_dictation_text_log": str(LAST_DICTATION_TEXT_LOG_PATH),
            "dictation_text_logging_enabled": True,
            "settings_dir": str(SETTINGS_DIR),
            "settings_path": str(SETTINGS_PATH),
            "legacy_settings_path": str(LEGACY_SETTINGS_PATH),
        }
        if extra:
            snapshot["extra"] = extra
        diagnostics_json = json.dumps(snapshot, ensure_ascii=False, indent=2, default=_json_default)
        DIAGNOSTICS_LOG_PATH.write_text(diagnostics_json, encoding="utf-8")
        try:
            LAST_RUN_DIR.mkdir(parents=True, exist_ok=True)
            LAST_DIAGNOSTICS_LOG_PATH.write_text(diagnostics_json, encoding="utf-8")
        except Exception:
            pass
        log_info(
            "Diagnostics snapshot written",
            diagnostics_path=DIAGNOSTICS_LOG_PATH,
            last_diagnostics_path=LAST_DIAGNOSTICS_LOG_PATH,
        )
    except Exception as exc:
        log_exception("Could not write diagnostics snapshot", exc, diagnostics_path=DIAGNOSTICS_LOG_PATH)

def log_crash(message: str) -> None:
    """Write diagnostics to a stable user-visible log file without crashing UI."""
    log_event(logging.ERROR, message)


def install_exception_logging() -> None:
    def excepthook(exc_type, exc, tb):  # noqa: ANN001
        log_exception("Unhandled main exception", exc)
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = excepthook
    if hasattr(threading, "excepthook"):
        def threadhook(args):  # noqa: ANN001
            log_exception(
                "Unhandled thread exception",
                args.exc_value,
                thread=getattr(args.thread, "name", "thread"),
            )
        threading.excepthook = threadhook

