"""
VoiceFlow Offline — modular local dictation runtime
------------------------------------------------
Works without OpenAI API key.

Features:
- microphone selection
- local transcription with faster-whisper
- configurable global hotkey
- no floating bubble
- non-blocking recording / processing / result notifications
- realtime paste into the currently focused input field, so you can move the caret between windows while dictating
- optional insertion of edited text or raw recognized transcript
- stronger offline cleanup: filler removal, punctuation, commas, capitalization and optional LanguageTool grammar pass
- browser-safe Windows input injection without resizing maximized windows
- native Ctrl+V paste with SendInput fallback
- copy/paste from main window
- realtime-only insertion: confirmed speech chunks are pasted into the current cursor location while recording, no final paste after stop
- settings persistence between launches
- per-launch logs in voiceflow_logs/run_YYYY-MM-DD_HH-MM-SS_PID and quick latest logs in voiceflow_logs/_last_run
- extra hotkey_trace.jsonl diagnostics for F9 repeat-start/stop problems
- optional Windows 11 startup via HKCU Run registry key

Install:
    pip install sounddevice numpy faster-whisper pyperclip pyautogui keyboard
    # optional stronger grammar:
    pip install language-tool-python

Run:
    python voiceflow.py

Notes:
- The first transcription downloads the local Whisper model once.
- On Windows, the keyboard package may require admin permissions for global hotkeys on some systems.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import re
import subprocess
import sys
import time
import wave
import queue
import tempfile
import threading
import traceback
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional


def get_app_dir() -> Path:
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        module_path = Path(__file__).resolve()
        package_dir = module_path.parent
        if package_dir.name == "voiceflow_app":
            return package_dir.parent
        return package_dir
    except Exception:
        return Path.cwd()


APP_DIR = get_app_dir()
LOG_DIR = APP_DIR / "voiceflow_logs"
RUN_LOG_STAMP = time.strftime("%Y-%m-%d_%H-%M-%S")
RUN_LOG_DIR = LOG_DIR / f"run_{RUN_LOG_STAMP}_{os.getpid()}"
LAST_RUN_DIR = LOG_DIR / "_last_run"
LAST_RUN_POINTER_PATH = LOG_DIR / "_last_run_path.txt"
APP_LOG_PATH = RUN_LOG_DIR / "voiceflow.txt"
CRASH_LOG_PATH = RUN_LOG_DIR / "crash.txt"
DIAGNOSTICS_LOG_PATH = RUN_LOG_DIR / "diagnostics.json"
DICTATION_TEXT_LOG_PATH = RUN_LOG_DIR / "dictation_text.txt"
CATEGORY_LOG_FILES = {
    "hotkeys": "hotkeys.jsonl",
    # Ultra-detailed hotkey trace for debugging "F9 works once / does not stop / does not start again".
    # This file is intentionally separate so it can be sent to AI without digging through the main log.
    "hotkey_trace": "hotkey_trace.jsonl",
    "recording": "recording_state.jsonl",
    "notifications": "notifications.jsonl",
    "streaming": "streaming.jsonl",
    "worker_queue": "worker_queue.jsonl",
    "insertion": "insertion.jsonl",
}
CATEGORY_LOG_PATHS = {name: RUN_LOG_DIR / filename for name, filename in CATEGORY_LOG_FILES.items()}
LAST_APP_LOG_PATH = LAST_RUN_DIR / "voiceflow.txt"
LAST_CRASH_LOG_PATH = LAST_RUN_DIR / "crash.txt"
LAST_DIAGNOSTICS_LOG_PATH = LAST_RUN_DIR / "diagnostics.json"
LAST_DICTATION_TEXT_LOG_PATH = LAST_RUN_DIR / "dictation_text.txt"
LAST_CATEGORY_LOG_PATHS = {name: LAST_RUN_DIR / filename for name, filename in CATEGORY_LOG_FILES.items()}
MAX_RUN_LOG_DIRS = 80
SETTINGS_DIR = APP_DIR / "voiceflow_settings"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
LEGACY_SETTINGS_PATH = Path.home() / ".voiceflow_offline_settings.json"
_LOGGING_READY = False
_DICTATION_LOG_LOCK = threading.Lock()
_CATEGORY_LOG_LOCK = threading.Lock()
IS_WINDOWS = sys.platform.startswith("win")
_SINGLE_INSTANCE_MUTEX_HANDLE: Optional[int] = None
_SINGLE_INSTANCE_MUTEX_NAME = "Local\\VoiceFlowOffline_" + hashlib.sha256(str(APP_DIR).lower().encode("utf-8", errors="ignore")).hexdigest()[:16]
CUDA_REQUIRED_WINDOWS_DLLS = ("cublas64_12.dll", "cudnn_ops64_9.dll")
VOICE_CONTROL_COMMANDS = {
    "опусти строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "опусти на строку ниже": {"kind": "text", "value": "\n", "label": "новая строка"},
    "опусти ниже на строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "строка ниже": {"kind": "text", "value": "\n", "label": "новая строка"},
    "перейди на новую строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "перенеси строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "перенеси на новую строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "новая строка": {"kind": "text", "value": "\n", "label": "новая строка"},
    "следующая строка": {"kind": "text", "value": "\n", "label": "новая строка"},
    "ниже": {"kind": "text", "value": "\n", "label": "новая строка"},
    "строку вниз": {"kind": "text", "value": "\n", "label": "новая строка"},
    "новое предложение": {"kind": "text", "value": ". ", "label": "новое предложение"},
    "точка": {"kind": "text", "value": ".", "label": "точка"},
    "запятая": {"kind": "text", "value": ",", "label": "запятая"},
    "вопросительный знак": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "знак вопроса": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "поставь вопросительный знак": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "поставь знак вопроса": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "восклицательный знак": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "знак восклицания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "знак внимания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "поставь восклицательный знак": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "поставь знак восклицания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "поставь знак внимания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "новый абзац": {"kind": "text", "value": "\n\n", "label": "новый абзац"},
    "опусти абзац": {"kind": "text", "value": "\n\n", "label": "новый абзац"},
    "пробел": {"kind": "text", "value": " ", "label": "пробел"},
    "поставь пробел": {"kind": "text", "value": " ", "label": "пробел"},
    "таб": {"kind": "text", "value": "\t", "label": "табуляция"},
    "табуляция": {"kind": "text", "value": "\t", "label": "табуляция"},
    "удали символ": {"kind": "key", "value": "backspace", "label": "Backspace"},
    "удали букву": {"kind": "key", "value": "backspace", "label": "Backspace"},
    "стереть символ": {"kind": "key", "value": "backspace", "label": "Backspace"},
    "удали слово": {"kind": "hotkey", "value": ("ctrl", "backspace"), "label": "удалить слово"},
    "стереть слово": {"kind": "hotkey", "value": ("ctrl", "backspace"), "label": "удалить слово"},
    "отмени": {"kind": "hotkey", "value": ("ctrl", "z"), "label": "отмена"},
    "отмена": {"kind": "hotkey", "value": ("ctrl", "z"), "label": "отмена"},
    "выдели все": {"kind": "hotkey", "value": ("ctrl", "a"), "label": "выделить все"},
    "выделить все": {"kind": "hotkey", "value": ("ctrl", "a"), "label": "выделить все"},
    "скопируй": {"kind": "hotkey", "value": ("ctrl", "c"), "label": "копировать"},
    "копировать": {"kind": "hotkey", "value": ("ctrl", "c"), "label": "копировать"},
    "вставь": {"kind": "hotkey", "value": ("ctrl", "v"), "label": "вставить"},
    "вставить": {"kind": "hotkey", "value": ("ctrl", "v"), "label": "вставить"},
    "сохрани": {"kind": "hotkey", "value": ("ctrl", "s"), "label": "сохранить"},
    "сохранить": {"kind": "hotkey", "value": ("ctrl", "s"), "label": "сохранить"},
    "вверх": {"kind": "key", "value": "up", "label": "стрелка вверх"},
    "вниз": {"kind": "key", "value": "down", "label": "стрелка вниз"},
    "влево": {"kind": "key", "value": "left", "label": "стрелка влево"},
    "вправо": {"kind": "key", "value": "right", "label": "стрелка вправо"},
    "перейди в начало строки": {"kind": "key", "value": "home", "label": "начало строки"},
    "в начало строки": {"kind": "key", "value": "home", "label": "начало строки"},
    "начало строки": {"kind": "key", "value": "home", "label": "начало строки"},
    "перейди в конец строки": {"kind": "key", "value": "end", "label": "конец строки"},
    "в конец строки": {"kind": "key", "value": "end", "label": "конец строки"},
    "конец строки": {"kind": "key", "value": "end", "label": "конец строки"},
    "удалить строку": {
        "kind": "sequence",
        "value": (("key", "home"), ("hotkey", ("shift", "end")), ("key", "backspace"), ("key", "delete")),
        "label": "удалить строку",
    },
    "удали строку": {
        "kind": "sequence",
        "value": (("key", "home"), ("hotkey", ("shift", "end")), ("key", "backspace"), ("key", "delete")),
        "label": "удалить строку",
    },
    "очистить поле": {
        "kind": "sequence",
        "value": (("hotkey", ("ctrl", "a")), ("key", "backspace")),
        "label": "очистить поле",
        "reset_message_context": True,
    },
    "очисти поле": {
        "kind": "sequence",
        "value": (("hotkey", ("ctrl", "a")), ("key", "backspace")),
        "label": "очистить поле",
        "reset_message_context": True,
    },
    "отправить сообщение": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправь сообщение": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправить сообщения": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправь сообщения": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправить": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправь": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
}


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


def acquire_single_instance_lock() -> bool:
    """Return False when another VoiceFlow from this folder is already running."""
    global _SINGLE_INSTANCE_MUTEX_HANDLE
    if not IS_WINDOWS:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            return True
        _SINGLE_INSTANCE_MUTEX_HANDLE = handle
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log_warning(
                "Second application instance blocked",
                mutex_name=_SINGLE_INSTANCE_MUTEX_NAME,
                app_dir=APP_DIR,
            )
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            _SINGLE_INSTANCE_MUTEX_HANDLE = None
            return False
        log_info("Single-instance lock acquired", mutex_name=_SINGLE_INSTANCE_MUTEX_NAME)
        return True
    except Exception as exc:
        log_exception("Could not acquire single-instance lock; continuing", exc, mutex_name=_SINGLE_INSTANCE_MUTEX_NAME)
        return True


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


def normalize_voice_command_text(text: str) -> str:
    text = (text or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^\w\sа-яА-ЯёЁ-]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def voice_control_command_from_text(*texts: str) -> Optional[dict[str, object]]:
    for text in texts:
        normalized = normalize_voice_command_text(text)
        if not normalized:
            continue
        command = VOICE_CONTROL_COMMANDS.get(normalized)
        if command:
            return {"phrase": normalized, **command}
    return None


def split_unpunctuated_trailing_send_command(text: str) -> Optional[tuple[str, dict[str, object]]]:
    """Catch "message text send message" when Whisper missed the boundary.

    Keep this intentionally narrow: only Enter/send commands with at least two
    command words are allowed as unpunctuated suffixes. Navigation and delete
    commands should remain exact phrases to avoid accidental actions in normal
    dictation.
    """
    normalized_text = normalize_voice_command_text(text)
    if not normalized_text:
        return None
    raw_tokens = re.findall(r"\S+", text)
    if len(raw_tokens) < 3:
        return None

    command_items = sorted(
        VOICE_CONTROL_COMMANDS.items(),
        key=lambda item: len(normalize_voice_command_text(item[0])),
        reverse=True,
    )
    for phrase, command_data in command_items:
        if not (
            command_data.get("reset_message_context")
            and command_data.get("kind") == "key"
            and command_data.get("value") == "enter"
        ):
            continue
        normalized_phrase = normalize_voice_command_text(phrase)
        command_word_count = len(normalized_phrase.split())
        if command_word_count < 2:
            continue
        if not normalized_text.endswith(" " + normalized_phrase):
            continue
        if len(raw_tokens) <= command_word_count:
            continue
        prefix = " ".join(raw_tokens[:-command_word_count]).strip()
        if len(normalize_voice_command_text(prefix)) < 6:
            continue
        return prefix.rstrip(" ,;:"), {"phrase": normalized_phrase, **command_data}
    return None


def split_trailing_voice_control_command(text: str) -> tuple[str, Optional[dict[str, object]]]:
    text = re.sub(r"[ \t\r\f\v]+", " ", (text or "").strip())
    if not text:
        return "", None
    command = voice_control_command_from_text(text)
    if command:
        return "", command

    # Typical dictation shape: "message text. send message."  Treat only the
    # final sentence as a command to avoid accidental actions inside normal text.
    for match in reversed(list(re.finditer(r"(?<=[.!?…])\s+", text))):
        prefix = text[:match.start()].strip()
        suffix = text[match.end():].strip()
        if not prefix or not suffix:
            continue
        command = voice_control_command_from_text(suffix)
        if command:
            return prefix, command
    unpunctuated = split_unpunctuated_trailing_send_command(text)
    if unpunctuated:
        return unpunctuated
    return text, None


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


configure_logging()
write_diagnostics_snapshot({"phase": "module_import"})

import tkinter as tk
from tkinter import ttk, messagebox

try:
    import numpy as np
    import sounddevice as sd
except Exception as exc:  # pragma: no cover
    log_exception("Missing required audio dependencies", exc, install="pip install sounddevice numpy")
    print("Missing audio dependencies. Install: pip install sounddevice numpy")
    raise exc

try:
    import pyperclip
except Exception:
    pyperclip = None

try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    import keyboard
except Exception:
    keyboard = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None
    ImageDraw = None

try:
    from faster_whisper import WhisperModel
except Exception:
    WhisperModel = None


APP_NAME = "VoiceFlow Offline"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
LOCAL_WHISPER_MODEL = os.getenv("VOICEFLOW_LOCAL_MODEL", "medium")
WHISPER_MODEL_OPTIONS = ["tiny", "base", "small", "medium", "large-v3"]
QUALITY_OPTIONS = ["Быстро", "Точно", "Максимальная точность"]
INFERENCE_DEVICE_OPTIONS = ["auto", "cuda", "cpu"]
COMPUTE_TYPE_OPTIONS = ["auto", "int8_float16", "float16", "int8", "float32"]
STREAMING_SPEED_OPTIONS = ["Быстрее", "Баланс", "Качество"]
STREAMING_MODE_OPTIONS = ["Вставлять фрагментами"]
STREAMING_INTERVAL_OPTIONS = ["1", "2", "3", "4", "5", "6", "8", "10"]
# Stable streaming is phrase-based, not word-by-word. It waits for a short
# pause before committing a chunk to reduce hallucinations, broken words,
# duplicated fragments and bad punctuation from very short audio chunks.
STREAMING_MIN_SECONDS = 2.4
STREAMING_MAX_SECONDS = 9.5
STREAMING_TRAILING_SILENCE_SECONDS = 0.65
STREAM_FINISH_TIMEOUT_SECONDS = 12.0
# Realtime-only mode should not block the next hotkey while trying to
# recognize a final tail after stop. Logs showed F9 was detected, but the
# program stayed in finalizing/"Обрабатываю последний фрагмент" for up to
# the timeout, so the user thought the hotkey worked only once. Keep the
# hotkey responsive: confirmed chunks are inserted during recording, and the
# stop action immediately releases the UI for the next F9.
STREAM_FINAL_CHUNK_ON_STOP = False
# Pause-based punctuation: realtime text is inserted in chunks, so the best
# signal for sentence boundaries is not only Whisper punctuation, but also a
# real speech pause. Background PC/air-purifier noise can keep absolute audio
# levels non-zero, therefore we calculate an adaptive speech trailing silence
# metric and use about 2 seconds of speech pause as a strong sentence end.
STREAM_SENTENCE_PAUSE_SECONDS_FAST = 1.35
STREAM_SENTENCE_PAUSE_SECONDS_BALANCE = 1.70
STREAM_SENTENCE_PAUSE_SECONDS_QUALITY = 2.00
HOTKEY_DEBOUNCE_SECONDS = 0.18
HOTKEY_START_GUARD_SECONDS = 0.18
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "VoiceFlowOffline"
START_VISIBLE = any(arg.lower() in {"--show", "--window", "--visible"} for arg in sys.argv[1:])
START_MINIMIZED = not START_VISIBLE or any(arg.lower() in {"--minimized", "--background", "--startup"} for arg in sys.argv[1:])
DEFAULT_HOTKEY = "ctrl+shift+space"
HOTKEY_MODIFIERS = {"ctrl", "shift", "alt", "windows", "cmd", "command"}
HOTKEY_MODIFIER_ORDER = ["ctrl", "shift", "alt", "windows", "cmd", "command"]
HOTKEY_CAPTURE_CLEAR_KEYS = {"backspace", "delete", "esc"}
HOTKEY_SPECIAL_KEYS = {
    "space", "enter", "tab", "backspace", "delete", "insert", "home", "end",
    "pageup", "pagedown", "up", "down", "left", "right", "esc",
    "capslock", "numlock", "scrolllock", "printscreen", "pause",
}


WINDOWS_HOTKEY_POLL_INTERVAL_SECONDS = 0.025
# The polling backend must treat one physical press as one action. Some keyboards,
# focus changes or Windows hooks can flicker GetAsyncKeyState for a few milliseconds;
# stable release + minimum edge gap prevents one press from producing start/stop/start.
WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS = 0.10
WINDOWS_HOTKEY_MIN_EDGE_GAP_SECONDS = 0.55
WINDOWS_HOTKEY_HEARTBEAT_SECONDS = 5.0
WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS = 1.0
WINDOWS_HOTKEY_VK = {
    "space": (0x20,),
    "enter": (0x0D,),
    "tab": (0x09,),
    "backspace": (0x08,),
    "delete": (0x2E,),
    "insert": (0x2D,),
    "home": (0x24,),
    "end": (0x23,),
    "pageup": (0x21,),
    "pagedown": (0x22,),
    "up": (0x26,),
    "down": (0x28,),
    "left": (0x25,),
    "right": (0x27,),
    "esc": (0x1B,),
    "capslock": (0x14,),
    "numlock": (0x90,),
    "scrolllock": (0x91,),
    "printscreen": (0x2C,),
    "pause": (0x13,),
    "ctrl": (0x11, 0xA2, 0xA3),
    "shift": (0x10, 0xA0, 0xA1),
    "alt": (0x12, 0xA4, 0xA5),
    "windows": (0x5B, 0x5C),
    "cmd": (0x5B, 0x5C),
    "command": (0x5B, 0x5C),
}


def hotkey_token_to_windows_vk_options(token: str) -> tuple[int, ...]:
    """Return alternative VK codes that can satisfy one hotkey token."""
    token = (token or "").strip().lower()
    if token in WINDOWS_HOTKEY_VK:
        return WINDOWS_HOTKEY_VK[token]
    if re.fullmatch(r"f(?:[1-9]|1[0-2])", token):
        return (0x6F + int(token[1:]),)
    if re.fullmatch(r"[a-z]", token):
        return (ord(token.upper()),)
    if re.fullmatch(r"[0-9]", token):
        return (ord(token),)
    return ()


def hotkey_to_windows_vk_options(hotkey: str) -> list[tuple[int, ...]]:
    """Convert a normalized hotkey into VK alternatives for Windows polling."""
    result: list[tuple[int, ...]] = []
    for part in normalize_hotkey(hotkey).split("+"):
        options = hotkey_token_to_windows_vk_options(part)
        if not options:
            return []
        result.append(options)
    return result


def summarize_windows_hotkey_state(vk_options: list[tuple[int, ...]]) -> list[dict[str, object]]:
    """Return raw GetAsyncKeyState details for hotkey diagnostics.

    Example row: {"alternatives": [120], "down": true, "raw": {"120": -32767}}.
    This is only used in logs and is safe when Windows APIs are unavailable.
    """
    if not IS_WINDOWS:
        return []
    try:
        user32 = ctypes.windll.user32
        result: list[dict[str, object]] = []
        for alternatives in vk_options:
            raw: dict[str, int] = {}
            down = False
            for vk in alternatives:
                try:
                    value = int(user32.GetAsyncKeyState(int(vk)))
                except Exception:
                    value = 0
                raw[str(int(vk))] = value
                if value & 0x8000:
                    down = True
            result.append({
                "alternatives": [int(vk) for vk in alternatives],
                "down": down,
                "raw": raw,
            })
        return result
    except Exception:
        return []
try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover
    winreg = None  # type: ignore


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



def _quote_cmd_part(value: str) -> str:
    return '"' + value.replace('"', '\"') + '"'


def get_current_script_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(sys.argv[0]).resolve()


def get_startup_command() -> str:
    """Build a Windows startup command for the current script/exe."""
    if getattr(sys, "frozen", False):
        return f'{_quote_cmd_part(str(Path(sys.executable).resolve()))} --startup'

    script_path = get_current_script_path()
    python_exe = Path(sys.executable).resolve()
    # Prefer pythonw.exe to avoid a console window on Windows startup.
    if python_exe.name.lower() == "python.exe":
        pythonw = python_exe.with_name("pythonw.exe")
        if pythonw.exists():
            python_exe = pythonw
    return f'{_quote_cmd_part(str(python_exe))} {_quote_cmd_part(str(script_path))} --startup'


def is_windows_startup_enabled() -> bool:
    if not IS_WINDOWS or winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            value, _typ = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
        current_script = str(get_current_script_path()).lower()
        return current_script in str(value).lower()
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_windows_startup_enabled(enabled: bool) -> None:
    if not IS_WINDOWS or winreg is None:
        raise RuntimeError("Автозапуск доступен только на Windows")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, get_startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


@dataclass
class PasteTarget:
    """Window/control that had keyboard focus before dictation started."""

    foreground_hwnd: Optional[int] = None
    focus_hwnd: Optional[int] = None


if IS_WINDOWS:
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]
else:
    GUITHREADINFO = None  # type: ignore[assignment]


def _as_int_hwnd(value: object) -> Optional[int]:
    try:
        hwnd = int(value or 0)
        return hwnd or None
    except Exception:
        return None


def get_paste_target() -> PasteTarget:
    """Capture the foreground window and focused child control on Windows."""
    if not IS_WINDOWS:
        return PasteTarget()

    try:
        user32 = ctypes.windll.user32
        foreground_hwnd = _as_int_hwnd(user32.GetForegroundWindow())
        focus_hwnd = foreground_hwnd

        if foreground_hwnd:
            thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None)
            if thread_id and GUITHREADINFO is not None:
                gui = GUITHREADINFO()
                gui.cbSize = ctypes.sizeof(GUITHREADINFO)
                if user32.GetGUIThreadInfo(thread_id, ctypes.byref(gui)):
                    focus_hwnd = _as_int_hwnd(gui.hwndFocus) or foreground_hwnd

        return PasteTarget(foreground_hwnd=foreground_hwnd, focus_hwnd=focus_hwnd)
    except Exception:
        return PasteTarget()


def _tap_alt_to_unlock_foreground() -> None:
    """Windows sometimes blocks SetForegroundWindow; a quick Alt tap unlocks it."""
    if not IS_WINDOWS:
        return
    try:
        user32 = ctypes.windll.user32
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.01)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def restore_paste_target(target: Optional[PasteTarget]) -> bool:
    """Bring saved app/control back before sending Ctrl+V."""
    if not IS_WINDOWS or target is None or not target.foreground_hwnd:
        return False

    hwnd = target.foreground_hwnd
    focus_hwnd = target.focus_hwnd or hwnd

    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if not user32.IsWindow(hwnd):
            return False
        if focus_hwnd and not user32.IsWindow(focus_hwnd):
            focus_hwnd = hwnd

        SW_RESTORE = 9
        SW_SHOWMAXIMIZED = 3
        was_maximized = bool(user32.IsZoomed(hwnd))
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        current_thread_id = kernel32.GetCurrentThreadId()
        target_thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        foreground_hwnd = user32.GetForegroundWindow()
        foreground_thread_id = (
            user32.GetWindowThreadProcessId(foreground_hwnd, None)
            if foreground_hwnd
            else 0
        )

        attached_target = False
        attached_foreground = False

        if target_thread_id and target_thread_id != current_thread_id:
            attached_target = bool(user32.AttachThreadInput(current_thread_id, target_thread_id, True))
        if foreground_thread_id and foreground_thread_id != current_thread_id:
            attached_foreground = bool(user32.AttachThreadInput(current_thread_id, foreground_thread_id, True))

        try:
            _tap_alt_to_unlock_foreground()
            # Do not restore non-minimized windows: it can shrink maximized Chrome/Telegram.
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            if focus_hwnd:
                try:
                    user32.SetFocus(focus_hwnd)
                except Exception:
                    pass
            if was_maximized and not user32.IsZoomed(hwnd):
                user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
        finally:
            if attached_target:
                user32.AttachThreadInput(current_thread_id, target_thread_id, False)
            if attached_foreground:
                user32.AttachThreadInput(current_thread_id, foreground_thread_id, False)

        time.sleep(0.08)
        return True
    except Exception:
        return False


def is_paste_target_active(target: Optional[PasteTarget]) -> bool:
    if not IS_WINDOWS or target is None or not target.foreground_hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        foreground_hwnd = _as_int_hwnd(user32.GetForegroundWindow())
        return foreground_hwnd == target.foreground_hwnd
    except Exception:
        return False


def send_ctrl_v_native() -> bool:
    """Send Ctrl+V using Windows SendInput; fallback to keybd_event/pyautogui."""
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            ULONG_PTR = wintypes.WPARAM

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class INPUT_UNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002
            VK_CONTROL = 0x11
            VK_V = 0x56

            def key(vk: int, flags: int = 0) -> INPUT:
                item = INPUT()
                item.type = INPUT_KEYBOARD
                item.union.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
                return item

            inputs = (INPUT * 4)(
                key(VK_CONTROL),
                key(VK_V),
                key(VK_V, KEYEVENTF_KEYUP),
                key(VK_CONTROL, KEYEVENTF_KEYUP),
            )
            sent = user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))
            if sent == 4:
                return True
        except Exception:
            pass

        try:
            user32 = ctypes.windll.user32
            VK_CONTROL = 0x11
            VK_V = 0x56
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            time.sleep(0.025)
            user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.025)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.025)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            return True
        except Exception:
            pass

    if pyautogui is not None:
        pyautogui.hotkey("ctrl", "v")
        return True
    return False


def get_input_devices() -> list[tuple[int, str]]:
    """Return available microphone/input devices as (device_index, label)."""
    devices = sd.query_devices()
    try:
        default_input = sd.default.device[0]
    except Exception:
        default_input = None

    result: list[tuple[int, str]] = []
    for index, device in enumerate(devices):
        max_input_channels = int(device.get("max_input_channels", 0))
        if max_input_channels <= 0:
            continue

        hostapi_name = ""
        try:
            hostapi = sd.query_hostapis(int(device.get("hostapi", 0)))
            hostapi_name = str(hostapi.get("name", "")).strip()
        except Exception:
            hostapi_name = ""

        name = str(device.get("name", f"Input {index}")).strip()
        label = f"{index}: {name}"
        if hostapi_name:
            label += f" — {hostapi_name}"
        label += f" — {max_input_channels} ch"
        if default_input is not None and index == default_input:
            label += " — по умолчанию"

        result.append((index, label))

    return result


def normalize_hotkey(value: str) -> str:
    aliases = {
        "control": "ctrl",
        "win": "windows",
        "super": "windows",
        "return": "enter",
        "escape": "esc",
        "prior": "pageup",
        "next": "pagedown",
    }
    parts = []
    for part in value.strip().lower().split("+"):
        token = part.strip().replace(" ", "")
        if token:
            parts.append(aliases.get(token, token))
    if not parts:
        return DEFAULT_HOTKEY
    return canonical_hotkey(parts)


def is_valid_hotkey_non_modifier(token: str) -> bool:
    token = (token or "").strip().lower()
    if not token or token in HOTKEY_MODIFIERS:
        return False
    if token in HOTKEY_SPECIAL_KEYS:
        return True
    if re.fullmatch(r"f(?:[1-9]|1[0-2])", token):
        return True
    if re.fullmatch(r"[a-z0-9]", token):
        return True
    return False


def canonical_hotkey(parts: list[str]) -> str:
    seen: set[str] = set()
    modifiers_seen: set[str] = set()
    non_modifiers: list[str] = []
    for part in parts:
        token = part.strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        if token in HOTKEY_MODIFIERS:
            modifiers_seen.add(token)
        elif is_valid_hotkey_non_modifier(token):
            non_modifiers.append(token)

    ordered: list[str] = []
    for modifier in HOTKEY_MODIFIER_ORDER:
        if modifier in modifiers_seen:
            ordered.append(modifier)
    ordered.extend(non_modifiers)
    return "+".join(ordered) if ordered else DEFAULT_HOTKEY


def tk_event_to_hotkey_part(event: object) -> Optional[str]:
    keysym = str(getattr(event, "keysym", "") or "")
    char = str(getattr(event, "char", "") or "")
    aliases = {
        "Control_L": "ctrl",
        "Control_R": "ctrl",
        "Shift_L": "shift",
        "Shift_R": "shift",
        "Alt_L": "alt",
        "Alt_R": "alt",
        "Meta_L": "windows",
        "Meta_R": "windows",
        "Super_L": "windows",
        "Super_R": "windows",
        "Win_L": "windows",
        "Win_R": "windows",
        "Command": "cmd",
        "Command_L": "cmd",
        "Command_R": "cmd",
        "space": "space",
        "Return": "enter",
        "KP_Enter": "enter",
        "Escape": "esc",
        "Tab": "tab",
        "BackSpace": "backspace",
        "Delete": "delete",
        "Insert": "insert",
        "Home": "home",
        "End": "end",
        "Prior": "pageup",
        "Next": "pagedown",
        "Up": "up",
        "Down": "down",
        "Left": "left",
        "Right": "right",
        "Caps_Lock": "capslock",
        "Num_Lock": "numlock",
        "Scroll_Lock": "scrolllock",
        "Print": "printscreen",
        "Pause": "pause",
    }
    if keysym in aliases:
        return aliases[keysym]
    if len(keysym) >= 2 and keysym[0].lower() == "f" and keysym[1:].isdigit():
        return keysym.lower()
    if keysym.startswith("KP_") and len(keysym) == 4 and keysym[-1].isdigit():
        return keysym[-1]
    if len(keysym) == 1 and keysym.isascii() and keysym.isprintable():
        return keysym.lower()
    if len(char) == 1 and char.isascii() and char.isprintable() and not char.isspace():
        return char.lower()
    return None


def is_modifier_only_hotkey(value: str) -> bool:
    parts = normalize_hotkey(value).split("+")
    return bool(parts) and all(part in HOTKEY_MODIFIERS for part in parts)


def repair_unreliable_modifier_only_hotkey(value: str) -> tuple[str, bool]:
    """Return a reliable hotkey and whether it had to be repaired.

    Pure modifier combinations like Ctrl+Win or Ctrl+Shift are unreliable with
    global Windows hooks: they can fire once and then stop reaching the app,
    because the OS or another application treats them as modifier state rather
    than a complete shortcut. Require at least one normal key.
    """
    hotkey = normalize_hotkey(value)
    if is_modifier_only_hotkey(hotkey):
        return DEFAULT_HOTKEY, True
    return hotkey, False


def should_suppress_hotkey_keys(value: str) -> bool:
    """Do not suppress user hotkey keys at OS level.

    For single-key hotkeys such as F9, suppress=True can make the Windows
    hook/library miss a later press or keep the key state inconsistent in some
    foreground apps. We only need to observe the shortcut, not block it.
    """
    return False


def pretty_hotkey(value: str) -> str:
    names = {
        "ctrl": "Ctrl",
        "shift": "Shift",
        "alt": "Alt",
        "space": "Space",
        "windows": "Win",
        "cmd": "Cmd",
        "command": "Cmd",
    }
    parts = normalize_hotkey(value).split("+")
    return " + ".join(names.get(part, part.upper() if len(part) == 1 else part.title()) for part in parts)


@dataclass
class AppSettings:
    mode: str = "Чистый текст"
    language: str = "auto"
    privacy_mode: bool = True
    microphone_label: str = ""
    hotkey: str = DEFAULT_HOTKEY
    auto_paste_after_hotkey: bool = True
    # Realtime-only mode: this flag is kept for compatibility with older saved settings,
    # but final paste after stop is intentionally disabled.
    insert_edited_text: bool = True
    show_notifications: bool = True
    whisper_model: str = LOCAL_WHISPER_MODEL
    recognition_quality: str = "Максимальная точность"
    inference_device: str = "auto"
    compute_type: str = "auto"
    use_vad_filter: bool = True
    custom_terms: str = "ChatGPT, OpenAI, Telegram, WhatsApp, Gmail, Python, JavaScript, TypeScript, Make, n8n, Tilda, Reels, Instagram, PowerShell, Whisper"
    deep_grammar: bool = True
    realtime_streaming_mode: str = "Вставлять фрагментами"
    realtime_chunk_seconds: int = 2
    realtime_fast_quality: bool = True
    realtime_speed_profile: str = "Быстрее"
    launch_at_startup: bool = False
    window_geometry: str = "980x780"
    notification_x: Optional[int] = None
    notification_y: Optional[int] = None


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    language: str
    whisper_model: str
    recognition_quality: str
    inference_device: str
    compute_type: str
    use_vad_filter: bool
    custom_terms: str
    deep_grammar: bool
    realtime_chunk_seconds: int
    realtime_fast_quality: bool
    realtime_speed_profile: str


class SettingsStore:
    """Persistent settings stored next to the program in voiceflow_settings/settings.json."""

    @staticmethod
    def _ensure_settings_dir() -> None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, object]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings file must contain a JSON object")
        return data

    @staticmethod
    def _settings_from_data(data: dict[str, object]) -> AppSettings:
        defaults = asdict(AppSettings())
        defaults.update({key: value for key, value in data.items() if key in defaults})
        repaired_hotkey, was_repaired = repair_unreliable_modifier_only_hotkey(str(defaults.get("hotkey", DEFAULT_HOTKEY)))
        if was_repaired:
            log_warning(
                "Saved modifier-only hotkey repaired to reliable default",
                saved_hotkey=defaults.get("hotkey"),
                repaired_hotkey=repaired_hotkey,
                reason="Modifier-only hotkeys like Ctrl+Win can stop firing repeatedly on Windows",
            )
        defaults["hotkey"] = repaired_hotkey
        return AppSettings(**defaults)

    @staticmethod
    def _backup_broken_settings(path: Path) -> Optional[Path]:
        try:
            if not path.exists():
                return None
            SettingsStore._ensure_settings_dir()
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = SETTINGS_DIR / f"{path.stem}.broken_{stamp}{path.suffix or '.json'}"
            shutil.copy2(path, backup_path)
            return backup_path
        except Exception as exc:
            log_exception("Could not backup broken settings file", exc, settings_path=path)
            return None

    @staticmethod
    def load() -> AppSettings:
        SettingsStore._ensure_settings_dir()

        if SETTINGS_PATH.exists():
            try:
                data = SettingsStore._read_json_file(SETTINGS_PATH)
                log_info("Settings loaded", settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
                return SettingsStore._settings_from_data(data)
            except Exception as exc:
                backup_path = SettingsStore._backup_broken_settings(SETTINGS_PATH)
                log_exception(
                    "Could not load settings; using defaults",
                    exc,
                    settings_path=SETTINGS_PATH,
                    backup_path=backup_path,
                )
                return AppSettings()

        if LEGACY_SETTINGS_PATH.exists():
            try:
                data = SettingsStore._read_json_file(LEGACY_SETTINGS_PATH)
                settings = SettingsStore._settings_from_data(data)
                SettingsStore.save(settings)
                log_info(
                    "Legacy settings migrated to app settings folder",
                    legacy_settings_path=LEGACY_SETTINGS_PATH,
                    settings_path=SETTINGS_PATH,
                    settings_dir=SETTINGS_DIR,
                )
                return settings
            except Exception as exc:
                log_exception(
                    "Could not migrate legacy settings; using defaults",
                    exc,
                    legacy_settings_path=LEGACY_SETTINGS_PATH,
                    settings_path=SETTINGS_PATH,
                )
                return AppSettings()

        log_info("Settings file not found; using defaults", settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
        return AppSettings()

    @staticmethod
    def save(settings: AppSettings) -> None:
        try:
            SettingsStore._ensure_settings_dir()
            data = asdict(settings)
            tmp_path = SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, SETTINGS_PATH)
            log_info("Settings saved", settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
        except Exception as exc:
            log_exception("Could not save settings", exc, settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
            print(f"Could not save settings: {exc}", file=sys.stderr)
