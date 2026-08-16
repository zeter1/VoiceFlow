"""
VoiceFlow Offline — one-file local dictation app
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
    python voiceflow_realtime_current_cursor.py

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
        return Path(__file__).resolve().parent
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

class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self._stream: Optional[sd.InputStream] = None
        self._frames: list[np.ndarray] = []
        self._is_recording = False
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start(self) -> None:
        if self._is_recording:
            return
        self._frames.clear()

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log_warning("Audio input stream status", status=str(status))
                print(status, file=sys.stderr)
            with self._lock:
                self._frames.append(indata.copy())

        self.sample_rate = self._choose_sample_rate()
        log_info("Starting audio recorder", device=self.device, sample_rate=self.sample_rate, channels=self.channels)
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()
        self._is_recording = True
        log_info("Audio recorder started", device=self.device, sample_rate=self.sample_rate)

    def _choose_sample_rate(self) -> int:
        if self.device is None:
            return DEFAULT_SAMPLE_RATE
        try:
            info = sd.query_devices(self.device, "input")
            default_rate = int(float(info.get("default_samplerate", DEFAULT_SAMPLE_RATE)))
            return default_rate or DEFAULT_SAMPLE_RATE
        except Exception:
            return DEFAULT_SAMPLE_RATE

    def stop_discard(self) -> None:
        """Stop recording and discard buffered audio without creating a final WAV."""
        self.stop_stream_keep_frames()
        self.discard_frames()

    def stop_stream_keep_frames(self) -> None:
        """Stop the microphone stream but keep buffered frames for final processing."""
        if not self._is_recording:
            return
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._is_recording = False
        log_info("Audio stream stopped; buffered frames kept", frames=self.frames_count(), sample_rate=self.sample_rate)

    def discard_frames(self) -> None:
        with self._lock:
            discarded = len(self._frames)
            self._frames.clear()
        log_info("Audio frames discarded", frames=discarded)

    def stop_to_wav(self) -> Path:
        if not self._is_recording:
            raise RuntimeError("Recording is not active")
        assert self._stream is not None
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._is_recording = False

        with self._lock:
            if not self._frames:
                raise RuntimeError("Пустая запись: звук не был записан")
            audio = np.concatenate(self._frames, axis=0)

        if audio.size == 0:
            raise RuntimeError("Пустая запись: звук не был записан")

        audio = self._prepare_audio_for_whisper(audio)
        audio, wav_sample_rate = self._resample_for_whisper(audio, self.sample_rate)

        tmp_dir = Path(tempfile.gettempdir()) / "voiceflow_offline"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = tmp_dir / f"recording_{int(time.time())}.wav"

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(wav_sample_rate)
            wf.writeframes(audio.tobytes())

        log_info(
            "Recording saved to wav",
            wav_path=wav_path,
            source_sample_rate=self.sample_rate,
            wav_sample_rate=wav_sample_rate,
            samples=int(audio.size),
        )
        return wav_path

    def frames_count(self) -> int:
        with self._lock:
            return len(self._frames)

    def get_frames_since(self, frame_index: int) -> tuple[list[np.ndarray], int, int]:
        """Return a copy of recorded frames after frame_index for pseudo-streaming."""
        with self._lock:
            total = len(self._frames)
            safe_index = max(0, min(frame_index, total))
            frames = [frame.copy() for frame in self._frames[safe_index:total]]
            sample_rate = self.sample_rate
        return frames, total, sample_rate

    def frames_to_wav(self, frames: list[np.ndarray], prefix: str = "stream_chunk") -> Optional[Path]:
        """Save selected in-memory frames to a temporary WAV file for chunk transcription."""
        if not frames:
            return None
        audio = np.concatenate(frames, axis=0)
        if audio.size == 0:
            return None

        try:
            prepared = self._prepare_audio_for_whisper(audio)
        except Exception:
            # A streaming chunk can be only silence or too short; skip it quietly.
            return None
        prepared, wav_sample_rate = self._resample_for_whisper(prepared, self.sample_rate)

        tmp_dir = Path(tempfile.gettempdir()) / "voiceflow_offline"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = tmp_dir / f"{prefix}_{int(time.time() * 1000)}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(wav_sample_rate)
            wf.writeframes(prepared.tobytes())
        return wav_path

    def _resample_for_whisper(self, audio: np.ndarray, source_rate: int) -> tuple[np.ndarray, int]:
        """Convert normalized int16 audio to Whisper's native 16 kHz mono."""
        target_rate = DEFAULT_SAMPLE_RATE
        if source_rate <= 0 or source_rate == target_rate or audio.size == 0:
            return audio.astype(np.int16, copy=False), source_rate or target_rate

        source = audio.astype(np.float32)
        new_size = max(1, int(round(source.size * target_rate / float(source_rate))))
        old_positions = np.arange(source.size, dtype=np.float32)
        new_positions = np.linspace(0, source.size - 1, new_size, dtype=np.float32)
        resampled = np.interp(new_positions, old_positions, source)
        resampled = np.clip(resampled, -32768.0, 32767.0).astype(np.int16)
        return resampled, target_rate

    def _prepare_audio_for_whisper(self, audio: np.ndarray) -> np.ndarray:
        """Normalize microphone audio before transcription.

        This keeps the app dependency-light but improves accuracy noticeably:
        mono conversion, DC offset removal, conservative silence trimming and
        peak normalization. It does not apply aggressive noise gates, because
        those can destroy quiet consonants in Russian speech.
        """
        if audio.ndim > 1:
            audio = audio.astype(np.float32).mean(axis=1)
        else:
            audio = audio.astype(np.float32).reshape(-1)

        if audio.size == 0:
            raise RuntimeError("Пустая запись: звук не был записан")

        # Convert int16-like samples to -1..1.
        if np.nanmax(np.abs(audio)) > 2.0:
            audio = audio / 32768.0

        # Remove DC offset and trim long leading/trailing silence.
        audio = audio - float(np.mean(audio))
        abs_audio = np.abs(audio)
        peak = float(np.max(abs_audio)) if abs_audio.size else 0.0
        if peak < 0.002:
            raise RuntimeError("Запись слишком тихая: микрофон почти ничего не записал")

        # Trim only clear silence, with padding, so Whisper receives less noise.
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        threshold = max(0.003, min(0.025, rms * 0.45))
        active = np.where(abs_audio > threshold)[0]
        if active.size:
            pad = int(self.sample_rate * 0.20)
            start = max(0, int(active[0]) - pad)
            end = min(audio.size, int(active[-1]) + pad)
            if end > start:
                audio = audio[start:end]

        # Normalize to a safe peak.
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio / peak * 0.92

        audio = np.clip(audio, -1.0, 1.0)
        return (audio * 32767.0).astype(np.int16)


class LocalTranscriber:
    def __init__(self):
        self._models: dict[tuple[str, str, str], WhisperModel] = {}
        self._failed_backends: set[tuple[str, str, str]] = set()
        self._verified_cuda_compute_types: set[str] = set()
        self._lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self.active_backend_label = "not loaded"

    def _normalize_model_name(self, model_name: str) -> str:
        return model_name if model_name in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL

    def _device_compute_candidates(self, device_option: str, compute_type_option: str) -> list[tuple[str, str]]:
        device_option = device_option if device_option in INFERENCE_DEVICE_OPTIONS else "auto"
        compute_type_option = compute_type_option if compute_type_option in COMPUTE_TYPE_OPTIONS else "auto"
        cpu_compute = compute_type_option if compute_type_option in {"int8", "float32"} else "int8"

        if device_option == "cpu":
            # Some GPU compute types are invalid on CPU. Force a safe CPU backend.
            return [("cpu", cpu_compute)]

        missing_cuda_dlls = self._windows_missing_cuda_dlls()
        if missing_cuda_dlls:
            log_warning(
                "CUDA dependencies are not available; using CPU backend",
                requested_device=device_option,
                requested_compute=compute_type_option,
                required=list(CUDA_REQUIRED_WINDOWS_DLLS),
                missing=missing_cuda_dlls,
                hint="Install CUDA 12.x and add CUDA/cuDNN bin folders to PATH",
            )
            return [("cpu", cpu_compute)]

        if device_option == "cuda":
            # Even when the user explicitly selects CUDA, keep CPU as a safe fallback.
            # Without this, missing cuDNN/cuBLAS or an incompatible compute type can close/crash the app.
            if compute_type_option == "auto":
                return [("cuda", "int8_float16"), ("cuda", "float16"), ("cuda", "int8"), ("cpu", "int8")]
            return [("cuda", compute_type_option), ("cpu", "int8")]

        # auto: try GPU first, then gracefully fall back to CPU if CUDA/cuDNN is absent.
        if compute_type_option == "auto":
            return [("cuda", "int8_float16"), ("cuda", "float16"), ("cuda", "int8"), ("cpu", "int8")]
        return [("cuda", compute_type_option), ("cpu", "int8")]

    def _windows_dll_available(self, dll_name: str) -> bool:
        return not IS_WINDOWS or find_windows_dll(dll_name) is not None

    def _windows_missing_cuda_dlls(self) -> list[str]:
        if not IS_WINDOWS:
            return []
        return [dll_name for dll_name in CUDA_REQUIRED_WINDOWS_DLLS if find_windows_dll(dll_name) is None]

    def _cuda_preflight_ok(self, model_name: str, cand_compute: str) -> tuple[bool, str]:
        """Test CUDA model loading in a child process before using it in the UI process.

        Some broken CUDA/cuDNN/cuBLAS installations do not raise a normal Python
        exception; the native library can terminate python.exe. Running the first
        CUDA probe in a subprocess prevents the whole VoiceFlow window from closing.
        """
        if cand_compute in self._verified_cuda_compute_types:
            log_info(
                "CUDA runtime already verified; skipping repeated preflight",
                model=model_name,
                compute_type=cand_compute,
            )
            return True, ""

        missing_cuda_dlls = self._windows_missing_cuda_dlls()
        if missing_cuda_dlls:
            details = (
                "Missing CUDA 12 runtime DLLs in PATH: "
                + ", ".join(missing_cuda_dlls)
                + "; skipping slow CUDA preflight"
            )
            log_warning(
                "CUDA dependency missing; skipping CUDA preflight",
                model=model_name,
                compute_type=cand_compute,
                required=list(CUDA_REQUIRED_WINDOWS_DLLS),
                missing=missing_cuda_dlls,
                details=details,
            )
            return False, details

        log_info("CUDA preflight started", model=model_name, compute_type=cand_compute)
        code = (
            "import os, tempfile, wave\n"
            "from faster_whisper import WhisperModel\n"
            f"m = WhisperModel({model_name!r}, device='cuda', compute_type={cand_compute!r})\n"
            "p = os.path.join(tempfile.gettempdir(), 'voiceflow_cuda_preflight.wav')\n"
            "with wave.open(p, 'wb') as wf:\n"
            "    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(b'\\x00\\x00' * 16000)\n"
            "segments, info = m.transcribe(p, language='ru', beam_size=1, best_of=1, vad_filter=False, without_timestamps=True)\n"
            "list(segments)\n"
            "print('VOICEFLOW_CUDA_OK')\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
            if result.returncode == 0 and "VOICEFLOW_CUDA_OK" in result.stdout:
                self._verified_cuda_compute_types.add(cand_compute)
                log_info("CUDA preflight succeeded", model=model_name, compute_type=cand_compute)
                return True, ""
            details = (result.stderr or result.stdout or "unknown CUDA load error").strip()
            log_warning(
                "CUDA preflight failed",
                model=model_name,
                compute_type=cand_compute,
                returncode=result.returncode,
                details=details[-1200:],
            )
            return False, details[-1200:]
        except subprocess.TimeoutExpired:
            log_warning("CUDA preflight timed out", model=model_name, compute_type=cand_compute)
            return False, "CUDA preflight timeout: модель слишком долго загружалась в тестовом процессе"
        except Exception as exc:
            log_exception("CUDA preflight crashed", exc, model=model_name, compute_type=cand_compute)
            return False, str(exc)

    def _load_model(self, model_name: str, device: str = "auto", compute_type: str = "auto") -> WhisperModel:
        if WhisperModel is None:
            raise RuntimeError("Не установлен faster-whisper. Выполни: pip install faster-whisper")
        model_name = self._normalize_model_name(model_name)
        errors: list[str] = []
        for cand_device, cand_compute in self._device_compute_candidates(device, compute_type):
            key = (model_name, cand_device, cand_compute)
            if key in self._failed_backends:
                continue
            try:
                if cand_device == "cuda" and key not in self._models:
                    ok, details = self._cuda_preflight_ok(model_name, cand_compute)
                    if not ok:
                        self._failed_backends.add(key)
                        errors.append(f"{cand_device}/{cand_compute}: CUDA preflight failed: {details}")
                        log_warning(
                            "Skipping CUDA backend after failed preflight",
                            model=model_name,
                            device=cand_device,
                            compute_type=cand_compute,
                            details=details,
                        )
                        continue

                with self._lock:
                    if key not in self._models:
                        kwargs = {
                            "device": cand_device,
                            "compute_type": cand_compute,
                        }
                        if cand_device == "cpu":
                            # Ryzen 5 5600X has 6 cores / 12 threads; leave 1-2 threads for UI/system.
                            kwargs["cpu_threads"] = max(4, min(10, (os.cpu_count() or 8) - 1))
                        log_info("Loading Whisper model", model=model_name, device=cand_device, compute_type=cand_compute)
                        self._models[key] = WhisperModel(model_name, **kwargs)
                    self.active_backend_label = f"{cand_device}/{cand_compute}"
                    if cand_device == "cuda":
                        self._verified_cuda_compute_types.add(cand_compute)
                    log_info("Whisper backend selected", model=model_name, device=cand_device, compute_type=cand_compute)
                    return self._models[key]
            except BaseException as exc:
                self._failed_backends.add(key)
                err_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                errors.append(f"{cand_device}/{cand_compute}: {err_text}")
                log_exception("Whisper backend load failed", exc, model=model_name, device=cand_device, compute_type=cand_compute)
                continue
        raise RuntimeError(
            "Не удалось загрузить Whisper-модель ни на одном backend. "
            "Если хочешь GPU, проверь CUDA/cuDNN. Подробности: " + " | ".join(errors[-3:])
        )

    def transcribe(
        self,
        wav_path: Path,
        language: str = "auto",
        model_name: str = LOCAL_WHISPER_MODEL,
        quality: str = "Максимальная точность",
        custom_terms: str = "",
        use_vad_filter: bool = True,
        context_text: str = "",
        device: str = "auto",
        compute_type: str = "auto",
        streaming: bool = False,
    ) -> str:
        """Transcribe audio with faster-whisper.

        For realtime chunks we intentionally use faster decoding parameters than
        the final/full-recording path. Beam 8 on large-v3 is too slow for live
        typing on a Ryzen 5 5600X; GPU + int8_float16 and beam 1-3 is the right
        low-latency compromise.
        """
        started_at = time.perf_counter()
        model = self._load_model(model_name, device=device, compute_type=compute_type)
        lang = None if language == "auto" else language
        beam_size, best_of, patience = self._quality_params(quality, streaming=streaming)
        initial_prompt = self._build_initial_prompt(language, custom_terms, context_text)

        vad_parameters = {
            # Realtime chunks from a distant microphone with PC/air-purifier noise
            # need a softer VAD than final full-recording mode. A high threshold
            # can cut quiet Russian consonants and make chunks look like silence,
            # while no VAD lets Whisper hallucinate "Продолжение следует" / subtitles.
            "threshold": 0.35 if streaming else 0.45,
            "min_speech_duration_ms": 80 if streaming else 160,
            "min_silence_duration_ms": 260 if streaming else 650,
            "speech_pad_ms": 360 if streaming else 420,
        }

        kwargs = dict(
            language=lang,
            vad_filter=use_vad_filter,
            vad_parameters=vad_parameters if use_vad_filter else None,
            beam_size=beam_size,
            best_of=best_of,
            patience=patience,
            temperature=0.0,
            condition_on_previous_text=False if streaming else True,
            initial_prompt=initial_prompt,
            no_speech_threshold=0.64 if streaming else 0.58,
            compression_ratio_threshold=2.45 if streaming else 2.6,
            log_prob_threshold=-1.0,
        )
        if streaming:
            # Supported by current faster-whisper; removed below if user has an older build.
            kwargs["without_timestamps"] = True

        with self._transcribe_lock:
            try:
                segments, _info = model.transcribe(str(wav_path), **kwargs)
            except TypeError:
                kwargs.pop("without_timestamps", None)
                segments, _info = model.transcribe(str(wav_path), **kwargs)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        result = re.sub(r"\s+", " ", text).strip()
        log_info(
            "Transcription completed",
            wav_path=wav_path,
            streaming=streaming,
            language=language,
            model=model_name,
            quality=quality,
            backend=self.active_backend_label,
            elapsed_seconds=round(time.perf_counter() - started_at, 3),
            result_chars=len(result),
        )
        return result

    def _quality_params(self, quality: str, streaming: bool = False) -> tuple[int, int, float]:
        if streaming:
            if quality == "Быстро":
                return 1, 1, 1.0
            if quality == "Точно":
                return 3, 3, 1.0
            # In the "Качество" realtime profile the user prefers fewer missed
            # words over maximum speed. Beam 5 is noticeably more stable for
            # noisy/distant microphones than beam 3, while still staying usable
            # on CUDA.
            return 5, 5, 1.10
        if quality == "Быстро":
            return 3, 3, 1.0
        if quality == "Точно":
            return 5, 5, 1.05
        return 8, 8, 1.15

    def _build_initial_prompt(self, language: str, custom_terms: str, context_text: str = "") -> str:
        base_ru = (
            "Это качественная диктовка на русском языке, иногда с английскими терминами. "
            "Нужно точно распознать естественную речь, имена, названия сервисов и технические термины. "
            "Не придумывай слова, которых нет в аудио. Не добавляй фразы вроде 'спасибо за просмотр'. "
            "Сохраняй смысл без добавления фактов. "
        )
        base_en = (
            "This is a high-quality voice dictation. Recognize natural speech, names, "
            "product names and technical terms accurately. Do not add new facts or filler outro phrases. "
        )
        base = base_en if language == "en" else base_ru
        terms = ", ".join(t.strip() for t in custom_terms.split(",") if t.strip())
        if terms:
            base += f"Особенно важно сохранить написание терминов: {terms}. "
        context = re.sub(r"\s+", " ", context_text or "").strip()
        if context:
            base += f"Предыдущий контекст диктовки: {context[-360:]}. "
        return base[:1000]

class LocalTextCleaner:
    """Accuracy-oriented local editor for dictated text.

    Fully offline rules cannot equal a cloud LLM, but this pass is deliberately
    conservative and layered: spoken punctuation, filler removal, typo repair,
    sentence segmentation, comma heuristics, term preservation and optional
    LanguageTool grammar correction when the user installs language-tool-python.
    """

    RU_FILLERS = [
        "эээ", "ээ", "эм", "мм", "м-м", "типа", "короче", "как бы", "значит",
        "в общем", "вобщем", "это самое", "наверное наверное", "блин", "слушай",
        "так сказать", "вот", "ну вот", "ну типа", "как говорится", "скажем так",
        # "по сути" is intentionally not removed: in logs it often appears as
        # part of "по сути дела", and deleting only the first words leaves broken text.
        "в принципе", "реально", "прям", "прямо", "ладно",
    ]
    EN_FILLERS = ["um", "uh", "like", "you know", "i mean", "sort of", "kind of", "basically"]

    RU_REPLACEMENTS = {
        "вообщем": "в общем", "вобщем": "в общем", "во-первых": "во-первых", "во первых": "во-первых",
        "во-вторых": "во-вторых", "во вторых": "во-вторых", "щас": "сейчас", "счас": "сейчас",
        "сейчась": "сейчас", "чё": "что", "че": "что", "чтоб": "чтобы", "шо": "что",
        "пожалста": "пожалуйста", "пожалуста": "пожалуйста", "пжл": "пожалуйста",
        "извените": "извините", "извени": "извини", "спс": "спасибо", "оч": "очень",
        "сдесь": "здесь", "здраствуйте": "здравствуйте", "здравствуйтее": "здравствуйте",
        "до свиданья": "до свидания", "врят ли": "вряд ли", "врядли": "вряд ли",
        "придти": "прийти", "будующее": "будущее", "следущий": "следующий",
        "следущее": "следующее", "агенство": "агентство", "симпотичный": "симпатичный",
        "учавствовать": "участвовать", "учавствую": "участвую", "вообщето": "вообще-то",
        "что-ли": "что ли", "как-будто": "как будто", "всётаки": "всё-таки", "все таки": "всё-таки",
        "все-таки": "всё-таки", "по этому": "поэтому", "поэтому что": "потому что",
        "так же": "также", "то же": "тоже", "ни кто": "никто", "ни чего": "ничего",
        "ни когда": "никогда", "не где": "негде", "не зачем": "незачем",
        "на счёт": "насчёт", "не смотря на": "несмотря на", "имейл": "email", "емейл": "email",
    }

    PRESERVE_TERMS = {
        "chat gpt": "ChatGPT", "чат gpt": "ChatGPT", "чат джипити": "ChatGPT", "чат гпт": "ChatGPT",
        "chatgpt": "ChatGPT", "openai": "OpenAI", "опен ai": "OpenAI", "опенэйай": "OpenAI",
        "telegram": "Telegram", "телеграм": "Telegram", "whatsapp": "WhatsApp", "ватсап": "WhatsApp",
        "gmail": "Gmail", "google docs": "Google Docs", "гугл докс": "Google Docs",
        "notion": "Notion", "slack": "Slack", "crm": "CRM", "cursor": "Cursor",
        "vs code": "VS Code", "vscode": "VS Code", "python": "Python", "питон": "Python",
        "javascript": "JavaScript", "java script": "JavaScript", "typescript": "TypeScript",
        "api": "API", "ai": "AI", "make": "Make", "мейк": "Make", "n8n": "n8n", "эн эйт эн": "n8n",
        "tilda": "Tilda", "тильда": "Tilda", "reels": "Reels", "рилс": "Reels", "рилсы": "Reels",
        "instagram": "Instagram", "инстаграм": "Instagram", "windows": "Windows", "powershell": "PowerShell",
        "whisper": "Whisper", "faster-whisper": "faster-whisper", "fast whisper": "faster-whisper",
    }

    HARD_SENTENCE_CUES = [
        "дальше", "далее", "потом", "после этого", "теперь", "следующий момент",
        "следующее", "еще момент", "ещё момент", "важно", "главное", "итог", "в итоге",
        "по итогу", "например", "кстати", "отдельно", "при этом", "второй момент", "третий момент",
        "первое", "второе", "третье", "четвертое", "четвёртое", "пятое",
    ]

    DICTATION_SENTENCE_CUES = [
        "но зато", "но почему", "и меня это", "но", "зато", "да уж", "сейчас посмотрю",
        "у меня", "меня это", "сразу", "сюда", "почему", "зачем", "как вообще", "не будет ли",
        "может быть", "давайте", "я не могу", "иногда", "надо", "он не всегда", "он видел",
        "поэтому", "из-за этого", "в этом случае", "в любом случае", "получается", "получается что",
        "главное", "самое главное", "смотри", "давай", "давайте теперь", "сейчас", "сначала",
        "посмотрим", "вот почему", "по факту", "на самом деле", "это значит", "это означает",
        "это",
        "и еще", "и ещё", "еще", "ещё", "так вот", "в целом", "с одной стороны",
        "с другой стороны", "отдельный момент", "следующая проблема", "следующая штука",
    ]

    QUESTION_START_CUES = [
        "как", "почему", "зачем", "где", "куда", "когда", "откуда",
        "что если", "разве", "неужели", "не будет ли", "может быть", "но почему",
        "а почему", "а как", "а что", "сколько", "какой", "какая", "какое", "какие",
        "кто", "кому", "чей", "можно ли", "нужно ли", "надо ли", "стоит ли", "будет ли",
        "есть ли", "получится ли", "сможет ли", "могу ли", "можем ли", "как мне",
        "что делать если", "куда именно", "как лучше", "как правильно", "что можно",
        "что надо", "что нужно", "почему это", "как сделать",
    ]

    COMMA_BEFORE = [
        "но", "а", "что", "если", "когда", "потому что", "поскольку", "чтобы", "хотя", "который",
        "которая", "которое", "которые", "где", "куда", "откуда", "пока", "так как",
        "перед тем как", "после того как", "несмотря на то что", "для того чтобы",
        "из-за того что", "благодаря тому что", "чем", "словно", "будто", "как будто",
        "если бы", "даже если", "при условии что",
    ]

    def __init__(self):
        self._language_tool_cache: dict[str, object] = {}
        self._language_tool_failed: set[str] = set()

    def clean(self, raw_text: str, mode: str, language: str, custom_terms: str = "", deep_grammar: bool = True) -> str:
        text = raw_text.strip()
        if not text:
            return ""

        user_terms = self._parse_custom_terms(custom_terms)

        # Stage 1. Normalize speech/transcription artifacts.
        text = self._normalize_quotes_and_symbols(text)
        text = self._apply_spoken_punctuation(text)
        text = self._remove_repeated_spaces(text)
        text = self._fix_common_transcription_words(text)
        text = self._restore_preserved_terms(text, user_terms)
        text = self._remove_repeated_words(text)

        if mode != "Точно как сказано":
            text = self._remove_fillers(text)
            text = self._remove_command_phrases(text)
            text = self._remove_repeated_words(text)

        # Stage 2. Punctuation and sentence structure.
        text = self._normalize_spacing(text)
        text = self._insert_sentence_boundaries(text)
        text = self._improve_dictation_sentence_flow(text)
        text = self._split_overlong_sentences(text)
        text = self._insert_commas(text)
        text = self._fix_ru_microgrammar(text)
        text = self._fix_punctuation_collisions(text)
        text = self._question_punctuation(text)
        text = self._fix_punctuation_collisions(text)
        text = self._basic_punctuation(text)
        text = self._capitalize_sentences(text)
        text = self._restore_preserved_terms(text, user_terms)

        # Stage 3. Optional grammar engine.
        if deep_grammar:
            text = self._apply_language_tool_if_available(text, language)
            text = self._normalize_spacing(text)
            text = self._fix_punctuation_collisions(text)
            text = self._capitalize_sentences(text)
            text = self._restore_preserved_terms(text, user_terms)

        # Stage 4. Mode-specific formatting.
        if mode == "Коротко":
            text = self._make_short(text)
        elif mode == "Деловой стиль":
            text = self._business_style(text)
        elif mode == "Развернуто":
            text = self._expanded_style(text)
        elif mode == "Продающий стиль":
            text = self._sales_style(text)
        elif mode == "Для ChatGPT / AI-промпт":
            text = self._prompt_style(text)
        elif mode == "Для кода":
            text = self._coding_style(text)

        return text.strip()

    def _parse_custom_terms(self, custom_terms: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for term in re.split(r"[,;\n]", custom_terms or ""):
            term = term.strip()
            if len(term) >= 2:
                result[term.lower()] = term
        return result

    def _remove_repeated_spaces(self, text: str) -> str:
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n+ *", lambda m: "\n" * m.group(0).count("\n"), text)
        return text.strip()

    def _normalize_quotes_and_symbols(self, text: str) -> str:
        text = text.replace("…", "...")
        text = text.replace("—", " — ").replace("–", " — ")
        text = text.replace("« ", "«").replace(" »", "»")
        return text

    def _apply_spoken_punctuation(self, text: str) -> str:
        # Spoken punctuation is common in dictation and should not remain as words.
        replacements = [
            (r"(?i)\s+опусти\s+строку\s+", "\n"),
            (r"(?i)\s+строка\s+ниже\s+", "\n"),
            (r"(?i)\s+следующая\s+строка\s+", "\n"),
            (r"(?i)\s+перейди\s+на\s+новую\s+строку\s+", "\n"),
            (r"(?i)\s+новая\s+строка\s+", "\n"),
            (r"(?i)\s+опусти\s+абзац\s+", "\n\n"),
            (r"(?i)\s+новый\s+абзац\s+", "\n\n"),
            (r"(?i)\s+точка\s+", ". "),
            (r"(?i)\s+запятая\s+", ", "),
            (r"(?i)\s+(?:знак\s+вопроса|вопросительный\s+знак)\s+", "? "),
            (r"(?i)\s+(?:знак\s+восклицания|восклицательный\s+знак|знак\s+внимания)\s+", "! "),
            (r"(?i)\s+двоеточие\s+", ": "),
            (r"(?i)\s+точка\s+с\s+запятой\s+", "; "),
            (r"(?i)\s+вопросительный\s+знак\s+", "? "),
            (r"(?i)\s+восклицательный\s+знак\s+", "! "),
            (r"(?i)\s+тире\s+", " — "),
            (r"(?i)\s+открой\s+кавычки\s+", " «"),
            (r"(?i)\s+закрой\s+кавычки\s+", "» "),
        ]
        text = " " + text + " "
        for pattern, repl in replacements:
            text = re.sub(pattern, repl, text)
        return text.strip()

    def _fix_common_transcription_words(self, text: str) -> str:
        for wrong, right in sorted(self.RU_REPLACEMENTS.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = rf"(?i)(?<![\w-]){re.escape(wrong)}(?![\w-])"
            text = re.sub(pattern, right, text)
        return text

    def _restore_preserved_terms(self, text: str, user_terms: Optional[dict[str, str]] = None) -> str:
        terms = dict(self.PRESERVE_TERMS)
        if user_terms:
            terms.update(user_terms)
        for raw, proper in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = rf"(?i)(?<![\w-]){re.escape(raw)}(?![\w-])"
            text = re.sub(pattern, proper, text)
        return text

    def _remove_repeated_words(self, text: str) -> str:
        # Repeat pass handles "я я", "это это" and short accidental stutters.
        previous = None
        while previous != text:
            previous = text
            text = re.sub(r"(?i)\b([а-яёa-z0-9_-]{2,})\s+\1\b", r"\1", text)
        return text.strip()

    def _remove_fillers(self, text: str) -> str:
        fillers = sorted(self.RU_FILLERS + self.EN_FILLERS, key=len, reverse=True)
        for filler in fillers:
            text = re.sub(rf"(?i)(^|[\s,.;:!?]){re.escape(filler)}(?=\s|,|\.|;|:|!|\?|$)", r"\1", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        return text.strip(" ,")

    def _remove_command_phrases(self, text: str) -> str:
        replacements = [
            r"(?i)^напиши\s+(?:пожалуйста\s+)?(?:клиенту|ей|ему|им|мне)?\s*(?:что|такой текст|сообщение)?\s*",
            r"(?i)^скажи\s+(?:пожалуйста\s+)?(?:клиенту|ей|ему|им|мне)?\s*(?:что)?\s*",
            r"(?i)^сделай\s+(?:мне\s+)?(?:текст|сообщение|письмо|пост|заметку)\s*(?:что|про|о том что)?\s*",
            r"(?i)^напечатай\s+(?:пожалуйста\s+)?(?:что)?\s*",
            r"(?i)^вставь\s+(?:пожалуйста\s+)?(?:что)?\s*",
            r"(?i)^запиши\s+(?:пожалуйста\s+)?(?:что)?\s*",
            r"(?i)^write\s+(?:a\s+)?(?:message|email|post|note)\s*(?:that)?\s*",
        ]
        for pattern in replacements:
            text = re.sub(pattern, "", text).strip()
        return text

    def _normalize_spacing(self, text: str) -> str:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r"([,.!?;:])([^\s»\)])", r"\1 \2", text)
        text = re.sub(r"\s+—\s+", " — ", text)
        text = re.sub(r"\n\s+", "\n", text)
        return text.strip()

    def _insert_sentence_boundaries(self, text: str) -> str:
        text = re.sub(r"(?i)\s+(?:и|а)\s+потом\s+", ". Потом ", text)
        text = re.sub(r"(?i)\s+(?:и|а)\s+дальше\s+", ". Дальше ", text)
        text = re.sub(r"(?i)\s+(?:и|а)\s+после\s+этого\s+", ". После этого ", text)
        text = re.sub(r"(?i)\s+и\s+в\s+итоге\s+", ". В итоге ", text)
        for cue in sorted(self.HARD_SENTENCE_CUES, key=len, reverse=True):
            pattern = rf"(?i)(?<=[а-яёa-z0-9\)])\s+({re.escape(cue)})\s+"

            def hard_sentence_repl(match: re.Match[str], cue: str = cue) -> str:
                clause = re.split(r"[.!?\n]", match.string[:match.start()])[-1].strip()
                clause_lower = clause.lower()
                # Do not split short phrases like "А теперь", "давай теперь"
                # or "давайте дальше". Logs showed these becoming
                # "А. Теперь" / "Давай. Теперь" in realtime text.
                if cue in {"теперь", "дальше", "потом"}:
                    if len(clause) < 18 or re.match(r"^(?:а|и\s+)?давай(?:те)?(?:\s+\S+){0,6}$", clause_lower):
                        return f" {match.group(1).lower()} "
                return f". {match.group(1).capitalize()} "

            text = re.sub(pattern, hard_sentence_repl, text)
        text = re.sub(r"(?i)\b(?:и|а)\.\s+(Потом|Дальше|После этого|В итоге)\b", r". \1", text)
        return text

    def _improve_dictation_sentence_flow(self, text: str) -> str:
        # Realtime chunks are cleaned separately, so we add a small layer of
        # dictation-specific sentence cues that Whisper often leaves as commas.
        def sentence_cue_repl(match: re.Match[str]) -> str:
            cue = match.group(1)
            prefix = match.string[:match.start()]
            clause = re.split(r"[.!?\n]", prefix)[-1].strip()
            cue_lower = cue.lower()
            question_like = cue_lower in {
                "почему", "зачем", "как вообще", "не будет ли", "может быть",
                "а почему", "а как", "а что", "сколько", "куда именно",
            }
            last_word_match = re.search(r"(?i)([а-яёa-z]+)\s*$", clause)
            last_word = last_word_match.group(1).lower() if last_word_match else ""
            if last_word in {"и", "а", "но"} and not cue_lower.startswith(last_word + " "):
                return match.group(0)

            short_cue = cue_lower in {
                "давайте", "давай", "давайте теперь", "надо", "иногда", "он не всегда",
                "он видел", "у меня", "и меня это", "я не могу", "поэтому", "из-за этого",
                "в этом случае", "в любом случае", "получается", "получается что",
                "главное", "самое главное", "смотри", "сейчас", "сначала", "посмотрим",
                "вот почему", "по факту", "на самом деле", "это значит", "это означает",
                "и еще", "и ещё", "еще", "ещё", "так вот", "в целом", "с одной стороны",
                "с другой стороны", "отдельный момент", "следующая проблема", "следующая штука",
            }
            # Do not split indirect questions. Logs showed bad output like
            # "Но я не понимаю. Почему..." instead of "не понимаю, почему...".
            clause_lower = clause.lower().strip(" ,;:")
            if cue_lower in {"почему", "зачем", "как вообще", "а почему", "а как", "а что"}:
                if re.search(r"\b(?:не\s+понимаю|не\s+знаю|непонятно|не\s+понял|не\s+поняла|не\s+ясно)$", clause_lower):
                    return match.group(0)
            if cue_lower == "это" and clause.lower().startswith("давайте"):
                short_cue = True
            if cue_lower == "это" and not clause.lower().startswith("давайте") and len(clause) < 35:
                return match.group(0)
            if len(clause) < (12 if short_cue else 28) and not question_like:
                return match.group(0)
            return f". {cue[0].upper() + cue[1:]} "

        for cue in sorted(self.DICTATION_SENTENCE_CUES, key=len, reverse=True):
            pattern = rf"(?i)(?<![.!?\n])\s+({re.escape(cue)})\s+"
            text = re.sub(pattern, sentence_cue_repl, text)

        text = re.sub(r"(?i)\b(да уж|может быть|скорее всего|наверное|вероятно|кстати|если честно|честно говоря|по факту|на самом деле)\s+", lambda m: f"{m.group(1).capitalize()}, ", text)
        # "по сути дела" is a single colloquial phrase. Do not rewrite it
        # into the unnatural "По сути, дела" during realtime cleanup.
        text = re.sub(r"(?i)\bпо сути\s+(?!дела\b)", lambda m: f"{m.group(0).strip().capitalize()}, ", text)
        text = re.sub(r"(?i)\bсразу\s+то\s+что\b", "сразу то, что", text)
        text = re.sub(r"(?i)\bдело\s+в\s+том\s+что\b", "дело в том, что", text)
        text = re.sub(r"(?i)\bпотому\s+что\s+это\b", "потому что это", text)
        text = re.sub(r"(?i)\bэто\s+значит\s+что\b", "это значит, что", text)
        text = re.sub(r"(?i)\bэто\s+означает\s+что\b", "это означает, что", text)
        text = re.sub(r"(?i)\bполучается\s+что\b", "получается, что", text)
        text = re.sub(r"(?i)\bя\s+думаю\s+что\b", "я думаю, что", text)
        text = re.sub(r"(?i)\bмне\s+кажется\s+что\b", "мне кажется, что", text)
        text = re.sub(r"(?i)\bя\s+понимаю\s+что\b", "я понимаю, что", text)
        text = re.sub(r"(?i)\bя\s+вижу\s+что\b", "я вижу, что", text)
        text = re.sub(r"(?i)\bпроблема\s+в\s+том\s+что\b", "проблема в том, что", text)
        text = re.sub(r"(?i)\bвопрос\s+в\s+том\s+что\b", "вопрос в том, что", text)
        text = re.sub(r"(?i)\bситуация\s+в\s+том\s+что\b", "ситуация в том, что", text)
        text = re.sub(r"(?i)\bс\s+одной\s+стороны\s+", "с одной стороны, ", text)
        text = re.sub(r"(?i)\bс\s+другой\s+стороны\s+", "с другой стороны, ", text)
        text = re.sub(r"(?i)\bв\s+целом\s+", "в целом, ", text)
        text = re.sub(r"(?i)\bтак\s+вот\s+", "так вот, ", text)
        return text

    def _question_punctuation(self, text: str) -> str:
        parts = re.split(r"(?<=[.!?])\s+", text)
        result: list[str] = []
        for part in parts:
            sentence = part.strip()
            if not sentence:
                continue
            lower = sentence.lower()
            trailing_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", sentence))
            is_question = any(
                lower.startswith(cue + " ") or lower.startswith(cue + ",") or lower == cue
                for cue in self.QUESTION_START_CUES
            )
            # A bare "ли" is too broad for realtime dictation: phrases like
            # "то ли правила срезались" were incorrectly converted into
            # "срезались,?". Keep only clear question patterns.
            is_question = is_question or bool(re.search(
                r"(?i)\b(?:разве|неужели)\b|\b(?:можно|нужно|надо|стоит|будет|есть|получится|сможет|могу|можем)\s+ли\b",
                sentence[:120],
            ))
            if is_question and not trailing_ellipsis and len(sentence) <= 180 and not lower.startswith("как только"):
                # Do not leave broken combinations like ",?" or ";?" when a
                # streaming chunk ended with a comma.
                sentence = re.sub(r"[,:;]\s*$", "", sentence).rstrip()
                if sentence.endswith("."):
                    sentence = sentence[:-1].rstrip() + "?"
                elif not re.search(r"[!?…]$", sentence):
                    sentence = sentence.rstrip() + "?"
            result.append(sentence)
        return " ".join(result)

    def _split_overlong_sentences(self, text: str) -> str:
        parts = re.split(r"(?<=[.!?])\s+", text)
        result: list[str] = []
        for sentence in parts:
            if len(sentence) <= 170:
                result.append(sentence)
                continue
            sentence = re.sub(r"(?i)\s+(но|зато|при этом|поэтому|из-за этого|после этого|дальше|потом|в итоге|в этом случае|в любом случае|у меня|давайте|давай|иногда|надо|получается|главное|самое главное|смотри|сначала|сейчас|на самом деле)\s+", r". \1 ", sentence)
            sentence = re.sub(r"(?i)\s+(и еще|и ещё)\s+", r". Ещё ", sentence)
            result.extend(re.split(r"(?<=[.!?])\s+", sentence))
        return " ".join(part.strip() for part in result if part.strip())

    def _insert_commas(self, text: str) -> str:
        intro_words = [
            "пожалуйста", "кажется", "возможно", "наверное", "вероятно", "конечно",
            "к сожалению", "к счастью", "во-первых", "во-вторых", "с одной стороны", "с другой стороны",
            "честно говоря", "если честно", "на мой взгляд", "по-моему", "как минимум", "как правило",
            "кстати", "например", "скорее всего", "может быть", "по сути", "по факту", "на самом деле",
        ]
        for word in sorted(intro_words, key=len, reverse=True):
            if word == "по сути":
                pattern = rf"(?i)(^|[.!?]\s+)({re.escape(word)})(\s+)(?!дела\b)"
            else:
                pattern = rf"(?i)(^|[.!?]\s+)({re.escape(word)})(\s+)"
            text = re.sub(pattern, lambda m: f"{m.group(1)}{m.group(2).capitalize()}, ", text)

        middle_intro_words = [
            "кажется", "возможно", "наверное", "вероятно", "конечно", "к сожалению", "к счастью",
            "кстати", "например", "скорее всего", "может быть", "честно говоря", "если честно",
            "на мой взгляд", "по-моему", "как правило", "по сути", "по факту",
        ]
        for word in sorted(middle_intro_words, key=len, reverse=True):
            if word == "по сути":
                pattern = rf"(?i)(?<=[а-яёa-z0-9])\s+({re.escape(word)})\s+(?!дела\b)(?=[а-яёa-z0-9])"
            else:
                pattern = rf"(?i)(?<=[а-яёa-z0-9])\s+({re.escape(word)})\s+(?=[а-яёa-z0-9])"
            text = re.sub(pattern, r", \1, ", text)

        after_intro_words = ["например", "кстати", "во-первых", "во-вторых", "главное", "самое главное", "смотри", "слушай"]
        for word in sorted(after_intro_words, key=len, reverse=True):
            text = re.sub(rf"(?i)\b({re.escape(word)})\s+(?=[а-яёa-z0-9])", r"\1, ", text)

        text = re.sub(r"(?i)(?<![,.!?;:])\s+(то есть|то бишь|а именно)\s+", r", \1 ", text)
        text = re.sub(r"(?i)\b(дело|проблема|суть)\s+в\s+том\s+что\b", r"\1 в том, что", text)
        text = re.sub(r"(?i)\b(важно|главное)\s+то\s+что\b", r"\1 то, что", text)
        text = re.sub(r"(?i)\b(если|когда|пока|хотя)\s+([^.!?]{8,90}?)\s+(то|тогда)\s+", r"\1 \2, \3 ", text)
        for conj in sorted(self.COMMA_BEFORE, key=len, reverse=True):
            pattern = rf"(?i)(?<![,.!?;:])\s+({re.escape(conj)})\s+"
            text = re.sub(pattern, r", \1 ", text)
        text = re.sub(r"(?i)не только\s+(.+?)\s+но и\s+", r"не только \1, но и ", text)
        text = re.sub(r"(?i)как\s+(.{3,70}?)\s+так и\s+", r"как \1, так и ", text)
        text = re.sub(r"(?i)как\s+только\s+", "как только ", text)
        text = re.sub(r"(?i)\bпотому,\s+что\b", "потому что", text)
        text = re.sub(r"(?i)\bтак,\s+как\b", "так как", text)
        text = re.sub(r"(?i)\bкак,\s+будто\b", "как будто", text)
        text = re.sub(r"(?i)\bчто,\s+если\b", "что если", text)
        text = re.sub(r"(?i)\bи,\s+что\s+(делать|нужно|надо|можно|будет|получится|лучше)\b", r"и что \1", text)
        text = re.sub(r"(?i)\bчто\s+делать\s+если\b", "что делать, если", text)
        # Remove common false commas caused by the broad conjunction heuristic.
        text = re.sub(r"(?i)^А,\s+", "А ", text)
        text = re.sub(r"(?i)\bа,\s+(если|когда|как|почему|зачем|что|куда|где)\b", r"а \1", text)
        text = re.sub(r"(?i)\bи,\s+что\b", "и что", text)
        text = re.sub(r"(?i)\bну,\s+что\b", "ну что", text)
        text = re.sub(r"(?i)\bпо сути,\s+дела\b", lambda m: "По сути дела" if m.group(0)[0].isupper() else "по сути дела", text)
        text = re.sub(r"(?<=[а-яёa-z0-9])\s+По сути дела\b", " по сути дела", text)
        return text

    def _fix_ru_microgrammar(self, text: str) -> str:
        # Conservative local grammar normalizations for dictated Russian.
        text = re.sub(r"(?i)\bболее\s+точнее\b", "точнее", text)
        text = re.sub(r"(?i)\bболее\s+лучше\b", "лучше", text)
        text = re.sub(r"(?i)\bболее\s+хуже\b", "хуже", text)
        text = re.sub(r"(?i)\bболее\s+плохо\b", "хуже", text)
        text = re.sub(r"(?i)\bдовольно\s+таки\b", "довольно-таки", text)
        text = re.sub(r"(?i)\bвсе\s+таки\b", "всё-таки", text)
        text = re.sub(r"(?i)\bвсё\s+таки\b", "всё-таки", text)
        text = re.sub(r"(?i)\bшел\b", "шёл", text)
        text = re.sub(r"(?i)\bшла\b", "шла", text)
        text = re.sub(r"(?i)\bя\s+буду\s+смогу\b", "я смогу", text)
        text = re.sub(r"(?i)\bмы\s+будем\s+сможем\b", "мы сможем", text)
        text = re.sub(r"(?i)\bне\s+успеваю\s+сделать\b", "не успеваю сделать", text)
        text = re.sub(r"(?i)\bв\s+течении\b", "в течение", text)
        text = re.sub(r"(?i)\bпо\s+приезду\b", "по приезде", text)
        text = re.sub(r"(?i)\bсогласно\s+([а-яё]+ого)\b", r"согласно \1", text)
        return text

    def _fix_punctuation_collisions(self, text: str) -> str:
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r",\s*,+", ",", text)
        # Whisper often emits ellipsis for unfinished realtime chunks. For
        # dictation this usually becomes noisy ".." / "..." in the inserted
        # text, so collapse repeated dots to a single normal sentence dot.
        text = re.sub(r"(?:\.\s*){2,}", ".", text)
        text = re.sub(r"([.!?])\s*,", r"\1", text)
        text = re.sub(r",\s*([.!?])", r"\1", text)
        text = re.sub(r"\.\s*\?", "?", text)
        text = re.sub(r"\?\s*\.", "?", text)
        text = re.sub(r"!\s*\.", "!", text)
        text = re.sub(r"(?i)\bа,\s+(если|когда|как|почему|зачем|что|куда|где)\b", r"а \1", text)
        text = re.sub(r"(?i)\bи,\s+что\b", "и что", text)
        text = re.sub(r"(?i)\bну,\s+что\b", "ну что", text)
        text = re.sub(r"(?i)\bпо сути,\s+дела\b", lambda m: "По сути дела" if m.group(0)[0].isupper() else "по сути дела", text)
        text = re.sub(r"(?<=[а-яёa-z0-9])\s+По сути дела\b", " по сути дела", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\s+\n", "\n", text)
        return text.strip()

    def _basic_punctuation(self, text: str) -> str:
        if not re.search(r"[.!?…]$", text):
            text += "."
        return text

    def _capitalize_sentences(self, text: str) -> str:
        def cap_match(match: re.Match[str]) -> str:
            return match.group(1) + match.group(2).upper()
        text = text.strip()
        if text:
            text = text[0].upper() + text[1:]
        text = re.sub(r"(^|[.!?]\s+)([а-яёa-z])", cap_match, text)
        text = re.sub(r"(\n\s*)([а-яёa-z])", cap_match, text)
        return text

    def _apply_language_tool_if_available(self, text: str, language: str) -> str:
        lang = self._map_language_tool_code(language, text)
        if lang in self._language_tool_failed:
            return text
        try:
            import language_tool_python  # type: ignore
        except Exception:
            return text
        try:
            tool = self._language_tool_cache.get(lang)
            if tool is None:
                tool = language_tool_python.LanguageTool(lang)
                self._language_tool_cache[lang] = tool
            corrected = tool.correct(text)
            if isinstance(corrected, str) and corrected.strip():
                return corrected.strip()
        except Exception:
            self._language_tool_failed.add(lang)
        return text

    def _map_language_tool_code(self, language: str, text: str) -> str:
        if language == "en":
            return "en-US"
        if language == "es":
            return "es"
        if language == "fr":
            return "fr"
        if language == "de":
            return "de-DE"
        if language == "ru":
            return "ru-RU"
        return "ru-RU" if re.search(r"[а-яёА-ЯЁ]", text) else "en-US"

    def _make_short(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        short = " ".join(sentences[:2]).strip()
        if len(short) > 280:
            short = short[:277].rstrip() + "..."
        return short

    def _business_style(self, text: str) -> str:
        lower = text.lower()
        if len(text) > 55 and any(word in lower for word in ["клиент", "материал", "задач", "стоим", "отправ", "договор", "счет", "счёт"]):
            return f"Здравствуйте!\n\n{text}\n\nС уважением."
        return text

    def _expanded_style(self, text: str) -> str:
        return f"{text}\n\nДополнительно можно уточнить детали, сроки и ожидаемый результат."

    def _sales_style(self, text: str) -> str:
        return f"{text}\n\nЕсли вам актуально — напишите, и я подскажу лучший вариант под вашу задачу."

    def _prompt_style(self, text: str) -> str:
        return (
            "Создай результат по следующему запросу. "
            "Сохрани смысл, сделай ответ структурированным, понятным и полезным.\n\n"
            f"Запрос: {text}"
        )

    def _coding_style(self, text: str) -> str:
        return (
            "Сформируй техническое решение по задаче ниже. "
            "Опиши архитектуру, шаги реализации, возможные ошибки и пример кода.\n\n"
            f"Задача: {text}"
        )

class NotificationManager:
    """Small no-activate toast notification for recording/transcription states."""

    COLORS = {
        "idle": ("#111827", "#F9FAFB"),
        "recording": ("#B91C1C", "#FFFFFF"),
        "stopped": ("#374151", "#FFFFFF"),
        "processing": ("#1D4ED8", "#FFFFFF"),
        "success": ("#047857", "#FFFFFF"),
        "warning": ("#92400E", "#FFFFFF"),
        "error": ("#991B1B", "#FFFFFF"),
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.window: Optional[tk.Toplevel] = None
        self.label: Optional[tk.Label] = None
        self.after_id: Optional[str] = None
        self._last_kind = "idle"
        self.manual_position: Optional[tuple[int, int]] = None
        self.on_position_changed: Optional[Callable[[Optional[tuple[int, int]]], None]] = None
        self._drag_pointer_start: Optional[tuple[int, int]] = None
        self._drag_window_start: Optional[tuple[int, int]] = None
        self._dragging = False

    def show(
        self,
        message: str,
        kind: str = "idle",
        duration_ms: Optional[int] = 2200,
        force_recreate: bool = False,
    ) -> None:
        self._last_kind = kind
        bg, fg = self.COLORS.get(kind, self.COLORS["idle"])

        if force_recreate and self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            self.label = None

        if self.window is None or not self.window.winfo_exists():
            self.window = tk.Toplevel(self.root)
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            try:
                self.window.attributes("-alpha", 0.96)
            except Exception:
                pass

            frame = tk.Frame(self.window, bg=bg, padx=18, pady=12)
            frame.pack(fill=tk.BOTH, expand=True)
            self.label = tk.Label(
                frame,
                text=message,
                bg=bg,
                fg=fg,
                font=("Segoe UI", 11, "bold"),
                justify="left",
                anchor="w",
            )
            self.label.pack(fill=tk.BOTH, expand=True)
            self._bind_drag_handlers(self.window)
            self._bind_drag_handlers(frame)
            self._bind_drag_handlers(self.label)
            if not self._dragging:
                self._position_window()
            self._make_no_activate_on_windows()
            self._show_no_activate()
        else:
            assert self.label is not None
            parent = self.label.master
            parent.configure(bg=bg)
            self.label.configure(text=message, bg=bg, fg=fg)
            # During a drag the timer updates this notification every second.
            # Do not reposition the toast while the mouse is dragging it, or
            # the window can jump back and feel impossible to move.
            if not self._dragging:
                self._position_window()
            self._show_no_activate()

        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

        if duration_ms is not None:
            self.after_id = self.root.after(duration_ms, self.hide)
        log_category(
            "notifications",
            "shown",
            kind=kind,
            duration_ms=duration_ms,
            message=message,
            manual_position=self.manual_position,
            force_recreate=force_recreate,
        )

    def hide(self) -> None:
        self.after_id = None
        if self.window is not None and self.window.winfo_exists():
            try:
                self.window.withdraw()
                log_category("notifications", "hidden", kind=self._last_kind)
            except Exception:
                pass

    def destroy(self) -> None:
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            self.label = None

    def _position_window(self) -> None:
        if self.window is None:
            return
        if self._dragging:
            return
        self.window.update_idletasks()
        width = max(360, self.window.winfo_reqwidth())
        height = max(70, self.window.winfo_reqheight())
        if self.manual_position is not None:
            x, y = self.manual_position
        else:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(20, screen_w - width - 28)
            y = max(20, screen_h - height - 72)
        self.window.geometry(f"{width}x{height}{self._format_geometry_position(x, y)}")

    def _format_geometry_position(self, x: int, y: int) -> str:
        return f"{int(x):+d}{int(y):+d}"

    def reset_manual_position_if_mostly_offscreen(self, min_visible_width: int = 10, min_visible_height: int = 10) -> None:
        """Keep a manually dragged toast position unless it is fully unreachable.

        Older builds reset the position when the toast was close to the screen
        edge/taskbar. That made the recording notification jump back while the
        user was dictating. Now a dragged position is respected; we only clamp
        it if almost the whole toast is outside the visible screen. Double-click
        the toast to intentionally reset it to the default corner.
        """
        if self.manual_position is None:
            return
        try:
            x, y = self.manual_position
            if self.window is not None and self.window.winfo_exists():
                self.window.update_idletasks()
                width = max(360, self.window.winfo_reqwidth())
                height = max(70, self.window.winfo_reqheight())
            else:
                width = 360
                height = 70
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            visible_w = max(0, min(x + width, screen_w) - max(x, 0))
            visible_h = max(0, min(y + height, screen_h) - max(y, 0))
            if visible_w < min_visible_width or visible_h < min_visible_height:
                new_x = min(max(x, -(width - min_visible_width)), max(0, screen_w - min_visible_width))
                new_y = min(max(y, -(height - min_visible_height)), max(0, screen_h - min_visible_height))
                self.manual_position = (int(new_x), int(new_y))
                log_info(
                    "Notification manual position clamped because it was nearly offscreen",
                    old_x=x,
                    old_y=y,
                    new_x=new_x,
                    new_y=new_y,
                    width=width,
                    height=height,
                    visible_width=visible_w,
                    visible_height=visible_h,
                )
                self._emit_position_changed()
        except Exception as exc:
            log_exception("Could not validate notification position", exc)

    def _emit_position_changed(self) -> None:
        callback = self.on_position_changed
        if callback is None:
            return
        try:
            callback(self.manual_position)
        except Exception as exc:
            log_exception("Notification position callback failed", exc, manual_position=self.manual_position)

    def _bind_drag_handlers(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._start_drag, add="+")
        widget.bind("<B1-Motion>", self._drag_to, add="+")
        widget.bind("<ButtonRelease-1>", self._finish_drag, add="+")
        widget.bind("<Double-Button-1>", self._reset_manual_position, add="+")

    def _start_drag(self, event: tk.Event) -> str:
        if self.window is None or not self.window.winfo_exists():
            return "break"
        self._dragging = True
        self._drag_pointer_start = (int(event.x_root), int(event.y_root))
        self._drag_window_start = (self.window.winfo_x(), self.window.winfo_y())
        try:
            self._show_no_activate()
        except Exception:
            pass
        return "break"

    def _drag_to(self, event: tk.Event) -> str:
        if (
            self.window is None
            or not self.window.winfo_exists()
            or self._drag_pointer_start is None
            or self._drag_window_start is None
        ):
            return "break"
        pointer_x, pointer_y = self._drag_pointer_start
        window_x, window_y = self._drag_window_start
        new_x = window_x + int(event.x_root) - pointer_x
        new_y = window_y + int(event.y_root) - pointer_y
        self.manual_position = (new_x, new_y)
        self.window.geometry(self._format_geometry_position(new_x, new_y))
        return "break"

    def _finish_drag(self, event: tk.Event) -> str:
        if self.window is not None and self.window.winfo_exists():
            self.manual_position = (self.window.winfo_x(), self.window.winfo_y())
            log_info("Notification position changed", x=self.manual_position[0], y=self.manual_position[1])
            self._emit_position_changed()
        self._dragging = False
        self._drag_pointer_start = None
        self._drag_window_start = None
        return "break"

    def _reset_manual_position(self, event: tk.Event) -> str:
        self.manual_position = None
        self._dragging = False
        self._drag_pointer_start = None
        self._drag_window_start = None
        self._position_window()
        log_info("Notification position reset")
        self._emit_position_changed()
        return "break"

    def _show_no_activate(self) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        try:
            self._make_no_activate_on_windows()
            if IS_WINDOWS:
                self.window.update_idletasks()
                hwnd = int(self.window.winfo_id())
                user32 = ctypes.windll.user32
                SW_SHOWNOACTIVATE = 4
                HWND_TOPMOST = -1
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            else:
                self.window.deiconify()
                self.window.lift()
        except Exception:
            try:
                self.window.deiconify()
            except Exception:
                pass

    def _make_no_activate_on_windows(self) -> None:
        if not IS_WINDOWS or self.window is None:
            return
        try:
            self.window.update_idletasks()
            hwnd = int(self.window.winfo_id())
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = int(get_style(hwnd, GWL_EXSTYLE))
            set_style(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass


class TrayManager:
    """Optional Windows tray icon powered by pystray when available."""

    def __init__(self, app: "VoiceFlowOfflineApp"):
        self.app = app
        self.icon: object = None
        self.thread: Optional[threading.Thread] = None

    @property
    def available(self) -> bool:
        return pystray is not None and Image is not None and ImageDraw is not None

    def start(self) -> bool:
        if not self.available:
            log_warning(
                "Tray icon is not available; install optional dependencies",
                install="pip install pystray pillow",
            )
            return False
        if self.icon is not None:
            return True

        menu = pystray.Menu(  # type: ignore[union-attr]
            pystray.MenuItem("Открыть окно", self._open_window, default=True),  # type: ignore[union-attr]
            pystray.MenuItem("Скрыть окно", self._hide_window),  # type: ignore[union-attr]
            pystray.MenuItem("Начать / остановить запись", self._toggle_recording),  # type: ignore[union-attr]
            pystray.MenuItem("Выход", self._exit_app),  # type: ignore[union-attr]
        )
        self.icon = pystray.Icon(  # type: ignore[union-attr]
            "VoiceFlowOffline",
            self._create_icon_image(),
            APP_NAME,
            menu,
        )
        self.thread = threading.Thread(target=self.icon.run, name="voiceflow-tray", daemon=True)
        self.thread.start()
        log_info("Tray icon started")
        return True

    def stop(self) -> None:
        icon = self.icon
        self.icon = None
        if icon is not None:
            try:
                icon.stop()
                log_info("Tray icon stopped")
            except Exception as exc:
                log_exception("Could not stop tray icon", exc)

    def _create_icon_image(self) -> object:
        image = Image.new("RGBA", (64, 64), (14, 116, 144, 255))  # type: ignore[union-attr]
        draw = ImageDraw.Draw(image)  # type: ignore[union-attr]
        draw.ellipse((8, 8, 56, 56), fill=(8, 145, 178, 255))
        draw.rounded_rectangle((27, 14, 37, 39), radius=5, fill=(255, 255, 255, 255))
        draw.rectangle((30, 39, 34, 49), fill=(255, 255, 255, 255))
        draw.arc((18, 25, 46, 49), start=0, end=180, fill=(255, 255, 255, 255), width=4)
        draw.rectangle((22, 50, 42, 54), fill=(255, 255, 255, 255))
        return image

    def _open_window(self, _icon: object = None, _item: object = None) -> None:
        try:
            log_category("hotkey_trace", "tray_open_window_clicked")
            self.app.worker_queue.put(("external_show_window", "tray_menu"))
        except Exception as exc:
            log_exception("Tray open-window action failed", exc)

    def _hide_window(self, _icon: object = None, _item: object = None) -> None:
        try:
            log_category("hotkey_trace", "tray_hide_window_clicked")
            self.app.worker_queue.put(("external_hide_window", "tray_menu"))
        except Exception as exc:
            log_exception("Tray hide-window action failed", exc)

    def _toggle_recording(self, _icon: object = None, _item: object = None) -> None:
        try:
            target = get_paste_target()
            log_category(
                "hotkey_trace",
                "tray_toggle_clicked",
                target={"foreground_hwnd": target.foreground_hwnd, "focus_hwnd": target.focus_hwnd},
            )
            # Put the action into the Tk-polled worker queue instead of calling
            # root.after from the pystray thread. On some systems pystray callbacks
            # can arrive from a non-Tk thread and the menu click then looks ignored.
            self.app.worker_queue.put(("external_toggle_recording", ("tray_menu", target)))
        except Exception as exc:
            log_exception("Tray toggle action failed", exc)

    def _exit_app(self, _icon: object = None, _item: object = None) -> None:
        try:
            log_category("hotkey_trace", "tray_exit_clicked")
            self.app.worker_queue.put(("external_exit", "tray_menu"))
        except Exception as exc:
            log_exception("Tray exit action failed", exc)


class VoiceFlowOfflineApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x780")
        self.root.minsize(900, 700)

        self.settings = SettingsStore.load()
        if re.match(r"^\d+x\d+(?:[+-]\d+[+-]\d+)?$", self.settings.window_geometry or ""):
            self.root.geometry(self.settings.window_geometry)
        # The current version is realtime-only. Older saved settings may contain "Выкл"
        # or "Только превью" from previous builds; force the product behavior requested by user.
        self.settings.realtime_streaming_mode = "Вставлять фрагментами"
        if IS_WINDOWS:
            self.settings.launch_at_startup = is_windows_startup_enabled()
        log_info(
            "Application settings initialized",
            settings={
                "mode": self.settings.mode,
                "language": self.settings.language,
                "privacy_mode": self.settings.privacy_mode,
                "hotkey": self.settings.hotkey,
                "whisper_model": self.settings.whisper_model,
                "recognition_quality": self.settings.recognition_quality,
                "inference_device": self.settings.inference_device,
                "compute_type": self.settings.compute_type,
                "use_vad_filter": self.settings.use_vad_filter,
                "deep_grammar": self.settings.deep_grammar,
                "realtime_chunk_seconds": self.settings.realtime_chunk_seconds,
                "realtime_speed_profile": self.settings.realtime_speed_profile,
                "launch_at_startup": self.settings.launch_at_startup,
                "settings_path": str(SETTINGS_PATH),
            },
        )
        self.recorder = AudioRecorder()
        self.transcriber = LocalTranscriber()
        self.cleaner = LocalTextCleaner()
        self.notifications = NotificationManager(root)
        self._load_notification_position_from_settings()
        self.notifications.on_position_changed = self._on_notification_position_changed
        self.tray = TrayManager(self)
        self.tray_started = False
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_wav_path: Optional[Path] = None
        self.record_started_at: Optional[float] = None
        self.timer_job: Optional[str] = None
        self.hotkey_handle: object = None
        self.hotkey_poll_stop_event = threading.Event()
        self.hotkey_poll_thread: Optional[threading.Thread] = None
        self.hotkey_poll_hotkey = ""
        self.processing_origin = "main"
        self.last_result_ready = False
        self.paste_target: Optional[PasteTarget] = None
        self.recording_session_id = 0
        self.finalizing_recording = False
        self.streaming_stop_event = threading.Event()
        self.stream_context_reset_event = threading.Event()
        self.streaming_thread: Optional[threading.Thread] = None
        self.stream_last_frame_index = 0
        self.stream_inserted_any = False
        self.stream_inserted_text = ""
        self.stream_preview_raw_text = ""
        self.stream_preview_clean_text = ""
        self.hotkey_entry_capture_active = False
        self.hotkey_entry_pressed: set[str] = set()
        self.hotkey_entry_pressed_order: list[str] = []
        self.hotkey_entry_apply_job: Optional[str] = None
        self.hotkey_entry_previous_value = ""
        self.hotkey_entry_cleared_by_user = False
        self.microphone_apply_job: Optional[str] = None
        self.transcriber_warmup_job: Optional[str] = None
        self.pending_hotkey_start_target: Optional[PasteTarget] = None
        self.pending_hotkey_start_requested = False
        self.exit_requested = False
        self._suspend_hotkey_auto_apply = False
        self._suspend_microphone_auto_apply = False
        self.recording_start_in_progress = False
        self.hotkey_ignore_until = 0.0
        self.hotkey_last_handled_at = 0.0
        self.hotkey_last_accepted_at = 0.0
        self.hotkey_last_poll_press_at = 0.0
        self.hotkey_debug_sequence = 0
        self.hotkey_poll_generation = 0

        self.mode_var = tk.StringVar(value=self.settings.mode)
        self.language_var = tk.StringVar(value=self.settings.language)
        self.privacy_var = tk.BooleanVar(value=self.settings.privacy_mode)
        self.microphone_var = tk.StringVar(value=self.settings.microphone_label)
        self.microphone_options: list[tuple[int, str]] = []
        self.hotkey_var = tk.StringVar(value=self.settings.hotkey)
        self.pretty_hotkey_var = tk.StringVar(value=pretty_hotkey(self.settings.hotkey))
        self.auto_paste_hotkey_var = tk.BooleanVar(value=self.settings.auto_paste_after_hotkey)
        self.insert_edited_text_var = tk.BooleanVar(value=self.settings.insert_edited_text)
        self.show_notifications_var = tk.BooleanVar(value=self.settings.show_notifications)
        self.launch_at_startup_var = tk.BooleanVar(value=self.settings.launch_at_startup)
        self.whisper_model_var = tk.StringVar(value=self.settings.whisper_model if self.settings.whisper_model in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL)
        self.recognition_quality_var = tk.StringVar(value=self.settings.recognition_quality if self.settings.recognition_quality in QUALITY_OPTIONS else "Максимальная точность")
        self.inference_device_var = tk.StringVar(value=self.settings.inference_device if self.settings.inference_device in INFERENCE_DEVICE_OPTIONS else "auto")
        self.compute_type_var = tk.StringVar(value=self.settings.compute_type if self.settings.compute_type in COMPUTE_TYPE_OPTIONS else "auto")
        self.use_vad_filter_var = tk.BooleanVar(value=self.settings.use_vad_filter)
        self.custom_terms_var = tk.StringVar(value=self.settings.custom_terms)
        self.deep_grammar_var = tk.BooleanVar(value=self.settings.deep_grammar)
        self.realtime_streaming_mode_var = tk.StringVar(
            value="Вставлять фрагментами"
        )
        self.realtime_chunk_seconds_var = tk.StringVar(value=str(self.settings.realtime_chunk_seconds))
        self.realtime_fast_quality_var = tk.BooleanVar(value=self.settings.realtime_fast_quality)
        self.realtime_speed_profile_var = tk.StringVar(value=self.settings.realtime_speed_profile if self.settings.realtime_speed_profile in STREAMING_SPEED_OPTIONS else "Быстрее")
        self.status_var = tk.StringVar(value="Готово")
        self.timer_var = tk.StringVar(value="00:00")

        self._build_ui()
        self._bind_auto_apply_controls()
        self._setup_hotkey()
        self._poll_worker_queue()
        self._poll_recording_state_watchdog()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tray_started = self.tray.start()
        self._schedule_warmup_transcriber(1200)

    def _build_ui(self) -> None:
        pad = 12
        root = ttk.Frame(self.root, padding=pad)
        root.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(root, text="VoiceFlow Offline", font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            root,
            text="Локальная диктовка без OpenAI API key: запись → усиленная правка текста → вставка в активное поле.",
        )
        subtitle.pack(anchor="w", pady=(2, 12))

        controls = ttk.Frame(root)
        controls.pack(fill=tk.X, pady=(0, 10))

        self.record_btn = ttk.Button(controls, text="● Начать запись", command=lambda: self.toggle_recording("main"))
        self.record_btn.pack(side=tk.LEFT)

        ttk.Label(controls, textvariable=self.timer_var).pack(side=tk.LEFT, padx=(12, 16))
        ttk.Label(controls, text="Статус:").pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.LEFT, padx=(5, 20))

        ttk.Label(controls, text="Модель:").pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.whisper_model_var).pack(side=tk.LEFT, padx=(4, 20))

        settings = ttk.LabelFrame(root, text="Настройки", padding=pad)
        settings.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(settings, text="Режим редактирования:").grid(row=0, column=0, sticky="w")
        mode_box = ttk.Combobox(
            settings,
            textvariable=self.mode_var,
            values=[
                "Точно как сказано",
                "Чистый текст",
                "Деловой стиль",
                "Коротко",
                "Развернуто",
                "Продающий стиль",
                "Для ChatGPT / AI-промпт",
                "Для кода",
            ],
            state="readonly",
            width=30,
        )
        mode_box.grid(row=0, column=1, padx=(8, 24), sticky="w")
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Label(settings, text="Язык:").grid(row=0, column=2, sticky="w")
        lang_box = ttk.Combobox(
            settings,
            textvariable=self.language_var,
            values=["auto", "ru", "en", "es", "fr", "de"],
            state="readonly",
            width=10,
        )
        lang_box.grid(row=0, column=3, padx=(8, 24), sticky="w")
        lang_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Checkbutton(
            settings,
            text="Privacy mode (WAV)",
            variable=self.privacy_var,
            command=self._save_settings,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(settings, text="Микрофон:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.microphone_box = ttk.Combobox(
            settings,
            textvariable=self.microphone_var,
            state="readonly",
            width=76,
        )
        self.microphone_box.grid(
            row=1,
            column=1,
            columnspan=3,
            padx=(8, 24),
            pady=(10, 0),
            sticky="we",
        )
        self.microphone_box.bind("<<ComboboxSelected>>", lambda _event: self._schedule_microphone_auto_apply(immediate=True))

        ttk.Button(
            settings,
            text="Обновить",
            command=lambda: self.refresh_microphones(show_message=True),
        ).grid(row=1, column=4, sticky="w", pady=(10, 0))

        ttk.Label(settings, text="Горячая клавиша:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        hotkey_frame = ttk.Frame(settings)
        hotkey_frame.grid(row=2, column=1, columnspan=4, sticky="we", pady=(10, 0))

        self.hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var, width=24)
        self.hotkey_entry.pack(side=tk.LEFT)
        self.hotkey_entry.bind("<FocusIn>", self._begin_hotkey_entry_capture)
        self.hotkey_entry.bind("<FocusOut>", self._end_hotkey_entry_capture)
        self.hotkey_entry.bind("<KeyPress>", self._capture_hotkey_entry_keypress)
        self.hotkey_entry.bind("<KeyRelease>", self._capture_hotkey_entry_keyrelease)
        ttk.Button(hotkey_frame, text="Нажать сочетание", command=self.capture_hotkey).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(hotkey_frame, text="Ctrl+Shift+Space", command=lambda: self.set_hotkey_preset("ctrl+shift+space")).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(hotkey_frame, text="Очистить", command=self.clear_hotkey_field).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(hotkey_frame, text="Сейчас: ").pack(side=tk.LEFT, padx=(16, 0))
        ttk.Label(hotkey_frame, textvariable=self.pretty_hotkey_var).pack(side=tk.LEFT)

        recognition = ttk.LabelFrame(settings, text="Точность распознавания", padding=8)
        recognition.grid(row=3, column=0, columnspan=5, sticky="we", pady=(12, 0))

        ttk.Label(recognition, text="Whisper-модель:").grid(row=0, column=0, sticky="w")
        model_box = ttk.Combobox(
            recognition,
            textvariable=self.whisper_model_var,
            values=WHISPER_MODEL_OPTIONS,
            state="readonly",
            width=14,
        )
        model_box.grid(row=0, column=1, sticky="w", padx=(8, 18))
        model_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(recognition, text="Качество:").grid(row=0, column=2, sticky="w")
        quality_box = ttk.Combobox(
            recognition,
            textvariable=self.recognition_quality_var,
            values=QUALITY_OPTIONS,
            state="readonly",
            width=22,
        )
        quality_box.grid(row=0, column=3, sticky="w", padx=(8, 18))
        quality_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Checkbutton(
            recognition,
            text="VAD: обрезать тишину и шумные паузы",
            variable=self.use_vad_filter_var,
            command=self._save_settings,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(recognition, text="Устройство:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        device_box = ttk.Combobox(
            recognition,
            textvariable=self.inference_device_var,
            values=INFERENCE_DEVICE_OPTIONS,
            state="readonly",
            width=14,
        )
        device_box.grid(row=1, column=1, sticky="w", padx=(8, 18), pady=(8, 0))
        device_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(recognition, text="Compute:").grid(row=1, column=2, sticky="w", pady=(8, 0))
        compute_box = ttk.Combobox(
            recognition,
            textvariable=self.compute_type_var,
            values=COMPUTE_TYPE_OPTIONS,
            state="readonly",
            width=16,
        )
        compute_box.grid(row=1, column=3, sticky="w", padx=(8, 18), pady=(8, 0))
        compute_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(recognition, text="Словарь терминов:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        terms_entry = ttk.Entry(recognition, textvariable=self.custom_terms_var)
        terms_entry.grid(row=2, column=1, columnspan=4, sticky="we", padx=(8, 0), pady=(8, 0))
        terms_entry.bind("<FocusOut>", lambda _event: self._save_settings())
        recognition.columnconfigure(4, weight=1)

        streaming = ttk.LabelFrame(settings, text="Realtime-ввод текста", padding=8)
        streaming.grid(row=4, column=0, columnspan=5, sticky="we", pady=(12, 0))

        ttk.Label(streaming, text="Режим:").grid(row=0, column=0, sticky="w")
        ttk.Label(streaming, text="Только realtime-вставка фрагментами").grid(row=0, column=1, sticky="w", padx=(8, 18))

        ttk.Label(streaming, text="Интервал фрагмента:").grid(row=0, column=2, sticky="w")
        streaming_interval_box = ttk.Combobox(
            streaming,
            textvariable=self.realtime_chunk_seconds_var,
            values=STREAMING_INTERVAL_OPTIONS,
            state="readonly",
            width=8,
        )
        streaming_interval_box.grid(row=0, column=3, sticky="w", padx=(8, 18))
        streaming_interval_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Checkbutton(
            streaming,
            text="Стриминг в ускоренном режиме: быстрее, но менее точно",
            variable=self.realtime_fast_quality_var,
            command=self._transcriber_settings_changed,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(streaming, text="Профиль realtime:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        speed_profile_box = ttk.Combobox(
            streaming,
            textvariable=self.realtime_speed_profile_var,
            values=STREAMING_SPEED_OPTIONS,
            state="readonly",
            width=14,
        )
        speed_profile_box.grid(row=1, column=1, sticky="w", padx=(8, 18), pady=(8, 0))
        speed_profile_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(
            streaming,
            text=(
                "Оптимально для твоего Ryzen 5 5600X + GTX 1660 Super: устройство auto/cuda, compute auto/int8_float16, "
                "интервал 1–2 сек, профиль «Быстрее»."
            ),
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))
        streaming.columnconfigure(4, weight=1)

        behavior = ttk.LabelFrame(settings, text="Поведение после записи", padding=8)
        behavior.grid(row=5, column=0, columnspan=5, sticky="we", pady=(12, 0))

        ttk.Label(
            behavior,
            text="✓ Ввод только в realtime: фрагменты вставляются во время записи, финальная вставка после остановки отключена.",
        ).grid(row=0, column=0, columnspan=4, sticky="w")

        ttk.Checkbutton(
            behavior,
            text="Вставлять отредактированный текст: очистка, пунктуация и выбранный режим",
            variable=self.insert_edited_text_var,
            command=self._save_settings,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Checkbutton(
            behavior,
            text="Глубокая грамматика: дополнительные правила + LanguageTool, если установлен",
            variable=self.deep_grammar_var,
            command=self._save_settings,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Checkbutton(
            behavior,
            text="Показывать уведомления: запись / остановка / распознавание / вставка",
            variable=self.show_notifications_var,
            command=self._save_settings,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        startup_state = tk.NORMAL if IS_WINDOWS else tk.DISABLED
        ttk.Checkbutton(
            behavior,
            text="Запускать программу при старте Windows 11",
            variable=self.launch_at_startup_var,
            command=self.apply_startup_setting,
            state=startup_state,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Label(
            behavior,
            text="Рабочий сценарий: нажми горячую клавишу → говори → ставь курсор в любое поле/окно — realtime-текст пойдёт туда, где сейчас курсор.",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))

        settings.columnconfigure(1, weight=1)
        self.refresh_microphones(show_message=False)

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True)

        raw_frame = ttk.LabelFrame(body, text="Распознанный текст без редактирования", padding=8)
        raw_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.raw_text = tk.Text(raw_frame, height=7, wrap=tk.WORD)
        self.raw_text.pack(fill=tk.BOTH, expand=True)

        clean_frame = ttk.LabelFrame(body, text="Готовый отредактированный текст", padding=8)
        clean_frame.pack(fill=tk.BOTH, expand=True)
        self.clean_text = tk.Text(clean_frame, height=9, wrap=tk.WORD)
        self.clean_text.pack(fill=tk.BOTH, expand=True)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(12, 0))

        ttk.Button(actions, text="Скопировать отредактированный", command=lambda: self.copy_result(show_messages=True, edited=True)).pack(side=tk.LEFT)
        ttk.Button(actions, text="Скопировать без редактирования", command=lambda: self.copy_result(show_messages=True, edited=False)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Скопировать для вставки", command=self.paste_result).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Очистить", command=self.clear_texts).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Свернуть окно", command=self.hide_main_window).pack(side=tk.RIGHT)

        help_text = (
            "Первый запуск модели может занять несколько минут: она скачивается один раз. "
            "Если горячая клавиша не работает, запусти PowerShell/программу от имени администратора. Настройки сохраняются в voiceflow_settings, а логи каждого запуска — в отдельной папке voiceflow_logs/run_дата_время. Последний запуск дублируется в voiceflow_logs/_last_run. Для усиленной грамматики: pip install language-tool-python"
        )
        ttk.Label(root, text=help_text).pack(anchor="w", pady=(10, 0))

    def _bind_auto_apply_controls(self) -> None:
        self.microphone_var.trace_add("write", self._schedule_microphone_auto_apply)
        self.hotkey_var.trace_add("write", self._schedule_hotkey_auto_apply)

    def _state_snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        try:
            stream_alive = self.streaming_thread is not None and self.streaming_thread.is_alive()
        except Exception:
            stream_alive = False
        return {
            "session_id": self.recording_session_id,
            "is_recording": bool(self.recorder.is_recording),
            "finalizing": bool(self.finalizing_recording),
            "recording_start_in_progress": bool(self.recording_start_in_progress),
            "pending_hotkey_start": bool(self.pending_hotkey_start_requested),
            "hotkey_ignore_remaining": max(0.0, round(self.hotkey_ignore_until - now, 3)),
            "hotkey_last_handled_age": round(now - self.hotkey_last_handled_at, 3) if self.hotkey_last_handled_at else None,
            "hotkey_last_accepted_age": round(now - self.hotkey_last_accepted_at, 3) if self.hotkey_last_accepted_at else None,
            "hotkey_last_poll_press_age": round(now - self.hotkey_last_poll_press_at, 3) if self.hotkey_last_poll_press_at else None,
            "hotkey_poll_hotkey": self.hotkey_poll_hotkey,
            "hotkey_poll_thread_alive": bool(self.hotkey_poll_thread is not None and self.hotkey_poll_thread.is_alive()),
            "status": self.status_var.get(),
            "record_button": str(self.record_btn.cget("text")) if hasattr(self, "record_btn") else "",
            "streaming_thread_alive": stream_alive,
            "stream_inserted_any": bool(self.stream_inserted_any),
            "notification_kind": getattr(self.notifications, "_last_kind", ""),
        }

    def log_state(self, category: str, event: str, **context: object) -> None:
        context.setdefault("state", self._state_snapshot())
        log_category(category, event, **context)

    def _next_hotkey_debug_sequence(self) -> int:
        self.hotkey_debug_sequence += 1
        return self.hotkey_debug_sequence

    def _hotkey_target_snapshot(self, target: Optional[PasteTarget] = None) -> dict[str, object]:
        if target is None:
            try:
                target = get_paste_target()
            except Exception:
                target = None
        return {
            "foreground_hwnd": getattr(target, "foreground_hwnd", None),
            "focus_hwnd": getattr(target, "focus_hwnd", None),
        }

    def _load_notification_position_from_settings(self) -> None:
        try:
            x = self.settings.notification_x
            y = self.settings.notification_y
            if isinstance(x, int) and isinstance(y, int):
                self.notifications.manual_position = (x, y)
                log_info("Notification position loaded from settings", x=x, y=y)
        except Exception as exc:
            log_exception("Could not load notification position from settings", exc)

    def _on_notification_position_changed(self, position: Optional[tuple[int, int]]) -> None:
        try:
            if position is None:
                self.settings.notification_x = None
                self.settings.notification_y = None
            else:
                self.settings.notification_x = int(position[0])
                self.settings.notification_y = int(position[1])
            SettingsStore.save(self.settings)
            log_info("Notification position saved", position=position)
        except Exception as exc:
            log_exception("Could not save notification position", exc, position=position)

    def log_hotkey_trace(self, event: str, **context: object) -> None:
        """Write a very detailed hotkey trace for repeat-start/stop bugs."""
        context.setdefault("debug_seq", self._next_hotkey_debug_sequence())
        context.setdefault("hotkey", normalize_hotkey(self.hotkey_var.get()))
        context.setdefault("thread", threading.current_thread().name)
        context.setdefault("state", self._state_snapshot())
        log_category("hotkey_trace", event, **context)

    def notify(
        self,
        message: str,
        kind: str = "idle",
        duration_ms: Optional[int] = 2200,
        force_recreate: bool = False,
    ) -> None:
        self.log_state(
            "notifications",
            "notify_requested",
            kind=kind,
            duration_ms=duration_ms,
            enabled=bool(self.show_notifications_var.get()),
            message=message,
            force_recreate=force_recreate,
        )
        if self.show_notifications_var.get():
            self.notifications.show(message, kind=kind, duration_ms=duration_ms, force_recreate=force_recreate)
            if kind != "recording" and self.recorder.is_recording and duration_ms is not None:
                try:
                    self.root.after(max(100, int(duration_ms) + 80), self._restore_recording_notification_if_needed)
                except Exception:
                    pass

    def _format_recording_notification_message(self) -> str:
        if self.record_started_at is None:
            current = "00:00"
        else:
            elapsed = max(0, int(time.time() - self.record_started_at))
            minutes, seconds = divmod(elapsed, 60)
            current = f"{minutes:02d}:{seconds:02d}"
        return f"🎙 Идёт запись: {current}\nНажми {pretty_hotkey(self.hotkey_var.get())} ещё раз, чтобы остановить"

    def _show_recording_notification(self, force_recreate: bool = False) -> None:
        if not self.show_notifications_var.get() or not self.recorder.is_recording:
            return
        self.notifications.reset_manual_position_if_mostly_offscreen()
        self.notifications.show(
            self._format_recording_notification_message(),
            kind="recording",
            duration_ms=None,
            force_recreate=force_recreate,
        )

    def _restore_recording_notification_if_needed(self) -> None:
        if self.recorder.is_recording and not self.exit_requested:
            self._show_recording_notification(force_recreate=False)

    def refresh_microphones(self, show_message: bool = False) -> None:
        try:
            self.microphone_options = get_input_devices()
            labels = [label for _device_id, label in self.microphone_options]
            self.microphone_box["values"] = labels
            log_info("Microphone list refreshed", count=len(labels), show_message=show_message)

            if not labels:
                self.microphone_var.set("Микрофоны не найдены")
                self.recorder.device = None
                log_warning("No input microphones found")
                if show_message:
                    messagebox.showwarning(APP_NAME, "Микрофоны не найдены. Проверь подключение и разрешения Windows.")
                return

            current = self.microphone_var.get()
            if current not in labels:
                default_label = next((label for label in labels if "по умолчанию" in label), labels[0])
                self.microphone_var.set(default_label)

            self._apply_selected_microphone()

            if show_message:
                messagebox.showinfo(APP_NAME, "Список микрофонов обновлён.")
        except Exception as exc:
            log_exception("Could not refresh microphone list", exc)
            self._show_error("Не удалось получить список микрофонов", exc)

    def _cancel_microphone_auto_apply(self) -> None:
        if self.microphone_apply_job is None:
            return
        try:
            self.root.after_cancel(self.microphone_apply_job)
        except Exception:
            pass
        self.microphone_apply_job = None

    def _schedule_microphone_auto_apply(self, *_args: object, immediate: bool = False) -> None:
        if self._suspend_microphone_auto_apply:
            return
        self._cancel_microphone_auto_apply()
        delay_ms = 0 if immediate else 80
        self.microphone_apply_job = self.root.after(delay_ms, self._auto_apply_selected_microphone)

    def _auto_apply_selected_microphone(self) -> None:
        self.microphone_apply_job = None
        self._apply_selected_microphone(show_status=True)

    def _apply_selected_microphone(self, show_status: bool = False) -> None:
        selected_label = self.microphone_var.get()
        for device_id, label in self.microphone_options:
            if label == selected_label:
                self.recorder.device = device_id
                self.settings.microphone_label = selected_label
                log_info("Microphone selected", device_id=device_id, label=selected_label)
                self._save_settings()
                if show_status:
                    short_label = selected_label[:78] + "..." if len(selected_label) > 78 else selected_label
                    self.status_var.set(f"Микрофон применён: {short_label}")
                return
        self.recorder.device = None
        self.settings.microphone_label = ""
        log_warning("Selected microphone label not found; using default device", selected_label=selected_label)
        self._save_settings()
        if show_status:
            self.status_var.set("Микрофон не найден, используется устройство по умолчанию")

    def _cancel_hotkey_entry_apply(self) -> None:
        if self.hotkey_entry_apply_job is None:
            return
        try:
            self.root.after_cancel(self.hotkey_entry_apply_job)
        except Exception:
            pass
        self.hotkey_entry_apply_job = None

    def _set_hotkey_display_without_auto_apply(self, hotkey: str) -> None:
        self._suspend_hotkey_auto_apply = True
        try:
            self.hotkey_var.set(hotkey)
            self.pretty_hotkey_var.set(pretty_hotkey(hotkey) if hotkey else "не выбрана")
        finally:
            self._suspend_hotkey_auto_apply = False

    def _schedule_hotkey_auto_apply(self, *_args: object) -> None:
        if self._suspend_hotkey_auto_apply:
            return
        self.pretty_hotkey_var.set(pretty_hotkey(self.hotkey_var.get()) if self.hotkey_var.get().strip() else "не выбрана")
        if self.hotkey_entry_capture_active and self.hotkey_entry_pressed:
            return
        self._cancel_hotkey_entry_apply()
        self.hotkey_entry_apply_job = self.root.after(450, self._auto_apply_hotkey_from_var)

    def _auto_apply_hotkey_from_var(self) -> None:
        self.hotkey_entry_apply_job = None
        hotkey = normalize_hotkey(self.hotkey_var.get())
        if self.hotkey_handle is not None and hotkey == normalize_hotkey(self.settings.hotkey):
            self._set_hotkey_display_without_auto_apply(hotkey)
            self.status_var.set(f"Горячая клавиша уже применена: {pretty_hotkey(hotkey)}")
            return
        self.apply_hotkey_from_ui(show_message=False)

    def _begin_hotkey_entry_capture(self, _event: object = None) -> None:
        self.hotkey_entry_capture_active = True
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        self.hotkey_entry_previous_value = normalize_hotkey(self.hotkey_var.get())
        self.hotkey_entry_cleared_by_user = False
        self._cancel_hotkey_entry_apply()
        self._set_hotkey_display_without_auto_apply("")
        self.status_var.set("Нажми новое сочетание клавиш")

    def _end_hotkey_entry_capture(self, _event: object = None) -> None:
        if not self.hotkey_entry_capture_active:
            return
        self.hotkey_entry_capture_active = False
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        if not self.hotkey_var.get().strip() and not self.hotkey_entry_cleared_by_user:
            self._set_hotkey_display_without_auto_apply(self.hotkey_entry_previous_value)
            self.status_var.set(f"Горячая клавиша оставлена: {pretty_hotkey(self.hotkey_entry_previous_value)}")
            return
        self._schedule_hotkey_auto_apply()

    def _capture_hotkey_entry_keypress(self, event: object) -> str:
        self._cancel_hotkey_entry_apply()
        part = tk_event_to_hotkey_part(event)
        if part is None:
            self.status_var.set("Эта клавиша не подходит для горячей клавиши")
            return "break"
        if part in HOTKEY_CAPTURE_CLEAR_KEYS:
            self.clear_hotkey_field()
            return "break"

        if not self.hotkey_entry_pressed:
            self.hotkey_entry_pressed_order = []
        if part not in self.hotkey_entry_pressed:
            self.hotkey_entry_pressed.add(part)
            if part in HOTKEY_MODIFIERS:
                self.hotkey_entry_pressed_order.append(part)
            elif is_valid_hotkey_non_modifier(part):
                self.hotkey_entry_pressed_order.append(part)
            else:
                self.status_var.set("Используй латинскую букву, цифру, F-клавишу или служебную клавишу")
                return "break"

        hotkey = canonical_hotkey(self.hotkey_entry_pressed_order)
        self.hotkey_var.set(hotkey)
        self.pretty_hotkey_var.set(pretty_hotkey(hotkey))
        self.status_var.set(f"Выбрано сочетание: {pretty_hotkey(hotkey)}")
        return "break"

    def _capture_hotkey_entry_keyrelease(self, event: object) -> str:
        part = tk_event_to_hotkey_part(event)
        if part is not None:
            self.hotkey_entry_pressed.discard(part)

        if not self.hotkey_entry_pressed:
            raw_hotkey = self.hotkey_var.get().strip()
            if not raw_hotkey:
                return "break"
            hotkey = normalize_hotkey(raw_hotkey)
            self.hotkey_entry_pressed_order = []
            self._cancel_hotkey_entry_apply()
            self.hotkey_entry_apply_job = self.root.after(
                220,
                lambda hotkey=hotkey: self._apply_hotkey_from_entry_capture(hotkey),
            )
        return "break"

    def _finish_hotkey_entry_capture_mode(self, move_focus: bool = True) -> None:
        self.hotkey_entry_capture_active = False
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        self.hotkey_entry_cleared_by_user = False
        self.hotkey_entry_previous_value = normalize_hotkey(self.hotkey_var.get())
        if move_focus:
            try:
                self.root.focus_set()
            except Exception:
                pass

    def clear_hotkey_field(self) -> None:
        self._cancel_hotkey_entry_apply()
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        self.hotkey_entry_cleared_by_user = True
        self._set_hotkey_display_without_auto_apply("")
        self.status_var.set("Горячая клавиша очищена. Нажми новое сочетание.")
        try:
            self.hotkey_entry.focus_set()
        except Exception:
            pass

    def _apply_hotkey_from_entry_capture(self, hotkey: str) -> None:
        self.hotkey_entry_apply_job = None
        self._set_hotkey_display_without_auto_apply(normalize_hotkey(hotkey))
        self.apply_hotkey_from_ui(show_message=False)
        self._finish_hotkey_entry_capture_mode(move_focus=True)
        log_info("Hotkey captured from entry", hotkey=self.hotkey_var.get())

    def _setup_hotkey(self) -> None:
        requested_hotkey = self.hotkey_var.get()
        hotkey = normalize_hotkey(requested_hotkey)
        hotkey, repaired_modifier_only = repair_unreliable_modifier_only_hotkey(hotkey)
        if requested_hotkey.strip().lower() != hotkey or repaired_modifier_only:
            log_warning(
                "Hotkey normalized or repaired",
                requested=requested_hotkey,
                normalized=hotkey,
                repaired_modifier_only=repaired_modifier_only,
            )
        if repaired_modifier_only:
            self.status_var.set(
                f"Горячая клавиша {pretty_hotkey(requested_hotkey)} нестабильна. "
                f"Поставлена надёжная: {pretty_hotkey(hotkey)}"
            )

        # Remove the previous keyboard hook before configuring any new backend.
        # The last build used BOTH keyboard.add_hotkey and Windows polling. Logs
        # showed one physical F9 press was delivered twice: keyboard_hook started
        # recording, then windows_poll_detected_press immediately stopped it.
        try:
            if self.hotkey_handle is not None and keyboard is not None:
                keyboard.remove_hotkey(self.hotkey_handle)
        except Exception as exc:
            log_exception("Could not remove previous keyboard hotkey", exc, hotkey=hotkey)
        finally:
            self.hotkey_handle = None

        # On Windows use only the independent polling backend. Mixing it with
        # keyboard.add_hotkey causes duplicate callbacks from the same physical
        # key press in some foreground apps. Polling waits for key release, so
        # one F9 press = exactly one start/stop action.
        polling_started = self._restart_windows_hotkey_polling(hotkey)
        keyboard_registered = False
        trigger_on_release = False
        suppress_hotkey = False

        if IS_WINDOWS and polling_started:
            self.settings.hotkey = hotkey
            self._set_hotkey_display_without_auto_apply(hotkey)
            self._save_settings()
            log_info(
                "Global hotkey configured with Windows polling only",
                hotkey=hotkey,
                keyboard_registered=keyboard_registered,
                windows_polling_started=polling_started,
                trigger_on_release=trigger_on_release,
                suppress=suppress_hotkey,
                reason="Avoid duplicate keyboard_hook + windows_polling events for one physical press",
            )
            self.log_state(
                "hotkeys",
                "configured_windows_polling_only",
                hotkey=hotkey,
                keyboard_registered=keyboard_registered,
                windows_polling_started=polling_started,
            )
            return

        if keyboard is None:
            self.settings.hotkey = hotkey
            self._set_hotkey_display_without_auto_apply(hotkey)
            self._save_settings()
            if polling_started:
                self.status_var.set(f"Горячая клавиша установлена через Windows fallback: {pretty_hotkey(hotkey)}")
            else:
                self.status_var.set("Горячие клавиши недоступны: не установлен keyboard и Windows fallback не запустился")
            log_warning(
                "keyboard package is not installed and Windows polling is unavailable",
                hotkey=hotkey,
                polling_started=polling_started,
            )
            return

        try:
            def hotkey_callback() -> None:
                target = get_paste_target()
                log_info(
                    "Global hotkey callback received",
                    hotkey=hotkey,
                    source="keyboard_hook",
                    is_recording=self.recorder.is_recording,
                    finalizing=self.finalizing_recording,
                )
                log_category(
                    "hotkeys",
                    "callback_received",
                    hotkey=hotkey,
                    source="keyboard_hook",
                    is_recording=bool(self.recorder.is_recording),
                    finalizing=bool(self.finalizing_recording),
                    recording_start_in_progress=bool(self.recording_start_in_progress),
                    pending_hotkey_start=bool(self.pending_hotkey_start_requested),
                    hotkey_ignore_remaining=max(0.0, round(self.hotkey_ignore_until - time.monotonic(), 3)),
                )
                self.root.after(0, lambda: self._handle_global_hotkey(hotkey, target))

            try:
                self.hotkey_handle = keyboard.add_hotkey(
                    hotkey,
                    hotkey_callback,
                    suppress=False,
                    trigger_on_release=False,
                )
            except TypeError:
                self.hotkey_handle = keyboard.add_hotkey(
                    hotkey,
                    hotkey_callback,
                    suppress=False,
                )
            keyboard_registered = True
        except Exception as exc:
            self.hotkey_handle = None
            log_exception("Could not register global hotkey", exc, hotkey=hotkey)
            self.status_var.set("Горячая клавиша не применена")
            self._show_error(
                "Не удалось установить горячую клавишу. Попробуй другое сочетание или запуск от имени администратора",
                exc,
            )
            return

        self.settings.hotkey = hotkey
        self._set_hotkey_display_without_auto_apply(hotkey)
        self._save_settings()
        log_info(
            "Global hotkey configured with keyboard hook",
            hotkey=hotkey,
            keyboard_registered=keyboard_registered,
            windows_polling_started=polling_started,
            trigger_on_release=trigger_on_release,
            suppress=suppress_hotkey,
        )
        self.log_state(
            "hotkeys",
            "configured_keyboard_hook",
            hotkey=hotkey,
            keyboard_registered=keyboard_registered,
            windows_polling_started=polling_started,
        )

    def _restart_windows_hotkey_polling(self, hotkey: str) -> bool:
        """Start a Windows API backend that detects the hotkey by key state.

        Diagnostic build: writes hotkey_trace.jsonl with exact press/release
        transitions, key-state raw values, debounce decisions and app state.
        """
        self._stop_windows_hotkey_polling()
        if not IS_WINDOWS:
            return False
        vk_options = hotkey_to_windows_vk_options(hotkey)
        if not vk_options:
            log_warning("Windows hotkey polling skipped: unsupported hotkey", hotkey=hotkey)
            return False

        stop_event = threading.Event()
        self.hotkey_poll_stop_event = stop_event
        self.hotkey_poll_hotkey = hotkey
        self.hotkey_poll_generation += 1
        generation = self.hotkey_poll_generation

        def poll_worker() -> None:
            user32 = ctypes.windll.user32
            was_down = False
            press_started_at: Optional[float] = None
            release_started_at: Optional[float] = None
            last_press_at = 0.0
            last_heartbeat_at = 0.0
            last_stuck_log_at = 0.0
            polls_total = 0
            polls_down = 0
            polls_up = 0
            log_info("Windows hotkey polling started", hotkey=hotkey, vk_options=vk_options, generation=generation)
            log_category("hotkeys", "windows_polling_started", hotkey=hotkey, vk_options=vk_options, generation=generation)
            self.log_hotkey_trace(
                "polling_started",
                hotkey=hotkey,
                generation=generation,
                vk_options=vk_options,
                poll_interval=WINDOWS_HOTKEY_POLL_INTERVAL_SECONDS,
                release_stable_seconds=WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS,
                min_edge_gap_seconds=WINDOWS_HOTKEY_MIN_EDGE_GAP_SECONDS,
                keyboard_module_loaded=keyboard is not None,
            )
            while not stop_event.wait(WINDOWS_HOTKEY_POLL_INTERVAL_SECONDS):
                now = time.monotonic()
                polls_total += 1
                try:
                    key_states = summarize_windows_hotkey_state(vk_options)
                    all_down = bool(key_states) and all(bool(item.get("down")) for item in key_states)
                    if all_down:
                        polls_down += 1
                    else:
                        polls_up += 1

                    if now - last_heartbeat_at >= WINDOWS_HOTKEY_HEARTBEAT_SECONDS:
                        last_heartbeat_at = now
                        self.log_hotkey_trace(
                            "polling_heartbeat",
                            hotkey=hotkey,
                            generation=generation,
                            all_down=all_down,
                            was_down=was_down,
                            polls_total=polls_total,
                            polls_down=polls_down,
                            polls_up=polls_up,
                            key_states=key_states,
                        )

                    if all_down:
                        release_started_at = None
                        if not was_down:
                            edge_gap = now - last_press_at if last_press_at else None
                            # Do not re-arm on micro flickers. A real second F9 press normally
                            # happens well after this gap, while bounce/auto-repeat happens faster.
                            if edge_gap is not None and edge_gap < WINDOWS_HOTKEY_MIN_EDGE_GAP_SECONDS:
                                self.log_hotkey_trace(
                                    "poll_press_ignored_too_close_to_previous_edge",
                                    hotkey=hotkey,
                                    generation=generation,
                                    edge_gap=round(edge_gap, 3),
                                    key_states=key_states,
                                )
                                was_down = True
                                press_started_at = now
                                continue

                            was_down = True
                            press_started_at = now
                            last_press_at = now
                            self.hotkey_last_poll_press_at = now
                            target = get_paste_target()
                            log_info(
                                "Windows hotkey polling detected press",
                                hotkey=hotkey,
                                is_recording=self.recorder.is_recording,
                                finalizing=self.finalizing_recording,
                            )
                            log_category(
                                "hotkeys",
                                "windows_poll_detected_press",
                                hotkey=hotkey,
                                is_recording=bool(self.recorder.is_recording),
                                finalizing=bool(self.finalizing_recording),
                                recording_start_in_progress=bool(self.recording_start_in_progress),
                                pending_hotkey_start=bool(self.pending_hotkey_start_requested),
                                hotkey_ignore_remaining=max(0.0, round(self.hotkey_ignore_until - time.monotonic(), 3)),
                            )
                            self.log_hotkey_trace(
                                "poll_press_detected",
                                hotkey=hotkey,
                                generation=generation,
                                key_states=key_states,
                                target=self._hotkey_target_snapshot(target),
                                edge_gap=round(edge_gap, 3) if edge_gap is not None else None,
                                will_schedule_handle=True,
                            )
                            try:
                                # Do not call Tk directly from the polling thread.
                                # Queue the event and let _poll_worker_queue handle it
                                # on the main Tk thread. This also fixes cases where
                                # F9 is detected in logs but the actual toggle is lost.
                                self.worker_queue.put(("global_hotkey_pressed", (hotkey, target, generation)))
                                self.log_hotkey_trace(
                                    "poll_press_queued_for_main_thread",
                                    hotkey=hotkey,
                                    generation=generation,
                                    target=self._hotkey_target_snapshot(target),
                                )
                            except Exception as exc:
                                self.log_hotkey_trace(
                                    "poll_press_queue_failed",
                                    hotkey=hotkey,
                                    generation=generation,
                                    error=str(exc),
                                )
                                break
                        else:
                            held_for = now - press_started_at if press_started_at else 0.0
                            if held_for >= WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS and now - last_stuck_log_at >= WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS:
                                last_stuck_log_at = now
                                self.log_hotkey_trace(
                                    "poll_key_still_down",
                                    hotkey=hotkey,
                                    generation=generation,
                                    held_for=round(held_for, 3),
                                    key_states=key_states,
                                )
                    else:
                        if was_down:
                            if release_started_at is None:
                                release_started_at = now
                                self.log_hotkey_trace(
                                    "poll_release_started",
                                    hotkey=hotkey,
                                    generation=generation,
                                    held_for=round(now - press_started_at, 3) if press_started_at else None,
                                    key_states=key_states,
                                )
                            elif now - release_started_at >= WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS:
                                was_down = False
                                self.log_hotkey_trace(
                                    "poll_release_confirmed_rearmed",
                                    hotkey=hotkey,
                                    generation=generation,
                                    release_stable_for=round(now - release_started_at, 3),
                                    held_for=round(now - press_started_at, 3) if press_started_at else None,
                                    key_states=key_states,
                                )
                                press_started_at = None
                                release_started_at = None
                except Exception as exc:
                    log_exception("Windows hotkey polling failed", exc, hotkey=hotkey, generation=generation)
                    try:
                        self.log_hotkey_trace("polling_exception", hotkey=hotkey, generation=generation, error=str(exc))
                    except Exception:
                        pass
                    time.sleep(0.25)
            log_info("Windows hotkey polling stopped", hotkey=hotkey, generation=generation)
            log_category("hotkeys", "windows_polling_stopped", hotkey=hotkey, generation=generation)
            try:
                self.log_hotkey_trace(
                    "polling_stopped",
                    hotkey=hotkey,
                    generation=generation,
                    polls_total=polls_total,
                    polls_down=polls_down,
                    polls_up=polls_up,
                )
            except Exception:
                pass

        self.hotkey_poll_thread = threading.Thread(
            target=poll_worker,
            name="voiceflow-hotkey-poll",
            daemon=True,
        )
        self.hotkey_poll_thread.start()
        return True

    def _stop_windows_hotkey_polling(self) -> None:
        thread = self.hotkey_poll_thread
        try:
            self.log_hotkey_trace(
                "polling_stop_requested",
                hotkey=self.hotkey_poll_hotkey,
                thread_alive=bool(thread is not None and thread.is_alive()),
            )
        except Exception:
            pass
        try:
            self.hotkey_poll_stop_event.set()
        except Exception:
            pass
        self.hotkey_poll_thread = None

    def _handle_global_hotkey(self, hotkey: str, target: Optional[PasteTarget]) -> None:
        now = time.monotonic()
        self.hotkey_last_handled_at = now
        target_snapshot = self._hotkey_target_snapshot(target)
        self.log_state("hotkeys", "handle_enter", hotkey=hotkey, target=target_snapshot)
        self.log_hotkey_trace(
            "handle_enter",
            hotkey=hotkey,
            target=target_snapshot,
            ignore_until=round(self.hotkey_ignore_until, 6),
            now=round(now, 6),
            ignore_remaining=round(max(0.0, self.hotkey_ignore_until - now), 3),
        )

        # Do not let a stale "hotkey entry capture" state block real global
        # hotkeys after the user already picked F9 and returned to another app.
        if self.hotkey_entry_capture_active and self.root.winfo_viewable():
            try:
                focused_widget = self.root.focus_get()
            except Exception:
                focused_widget = None
            self.log_hotkey_trace(
                "handle_capture_mode_check",
                hotkey=hotkey,
                focused_widget=str(focused_widget),
                hotkey_entry_active=bool(self.hotkey_entry_capture_active),
            )
            if focused_widget == self.hotkey_entry:
                log_info("Global hotkey ignored while editing hotkey field")
                self.log_state("hotkeys", "ignored_editing_hotkey_field", hotkey=hotkey, target=target_snapshot)
                self.log_hotkey_trace("handle_ignored_editing_hotkey_field", hotkey=hotkey, target=target_snapshot)
                return
            self._finish_hotkey_entry_capture_mode(move_focus=False)
            self.log_state("hotkeys", "stale_hotkey_capture_released", hotkey=hotkey, target=target_snapshot)
            self.log_hotkey_trace("handle_stale_hotkey_capture_released", hotkey=hotkey, target=target_snapshot)

        # Debounce must block duplicate callbacks from the same physical
        # key press, even if the first callback changed the state to recording.
        if now < self.hotkey_ignore_until:
            remaining = round(self.hotkey_ignore_until - now, 3)
            log_info(
                "Global hotkey ignored by debounce",
                hotkey=hotkey,
                ignore_for_seconds=remaining,
                is_recording=self.recorder.is_recording,
                finalizing=self.finalizing_recording,
                starting=self.recording_start_in_progress,
            )
            self.log_state(
                "hotkeys",
                "ignored_debounce",
                hotkey=hotkey,
                ignore_for_seconds=remaining,
                target=target_snapshot,
            )
            self.log_hotkey_trace(
                "handle_ignored_debounce",
                hotkey=hotkey,
                ignore_for_seconds=remaining,
                target=target_snapshot,
                key_states=summarize_windows_hotkey_state(hotkey_to_windows_vk_options(hotkey)),
            )
            return

        if self.recording_start_in_progress:
            self.hotkey_ignore_until = now + HOTKEY_START_GUARD_SECONDS
            log_info("Global hotkey ignored while recording start is in progress", hotkey=hotkey)
            self.log_state("hotkeys", "ignored_start_in_progress", hotkey=hotkey, target=target_snapshot)
            self.log_hotkey_trace(
                "handle_ignored_start_in_progress",
                hotkey=hotkey,
                target=target_snapshot,
                new_ignore_until=round(self.hotkey_ignore_until, 6),
            )
            return

        decision = "stop_recording" if self.recorder.is_recording else ("queue_start_after_finalizing" if self.finalizing_recording else "start_recording")
        self.hotkey_ignore_until = now + HOTKEY_DEBOUNCE_SECONDS
        self.hotkey_last_accepted_at = now
        self.log_state("hotkeys", "accepted", hotkey=hotkey, target=target_snapshot, decision=decision)
        self.log_hotkey_trace(
            "handle_accepted",
            hotkey=hotkey,
            target=target_snapshot,
            decision=decision,
            new_ignore_until=round(self.hotkey_ignore_until, 6),
            debounce_seconds=HOTKEY_DEBOUNCE_SECONDS,
        )
        self.toggle_recording("hotkey", target)

    def set_hotkey_preset(self, hotkey: str) -> None:
        self._set_hotkey_display_without_auto_apply(normalize_hotkey(hotkey))
        self.apply_hotkey_from_ui(show_message=False)
        self._finish_hotkey_entry_capture_mode(move_focus=True)

    def apply_hotkey_from_ui(self, show_message: bool = False) -> None:
        requested_hotkey = normalize_hotkey(self.hotkey_var.get())
        if not requested_hotkey:
            messagebox.showwarning(APP_NAME, "Введите сочетание, например ctrl+shift+space")
            return
        hotkey, repaired_modifier_only = repair_unreliable_modifier_only_hotkey(requested_hotkey)
        self._cancel_hotkey_entry_apply()
        self._set_hotkey_display_without_auto_apply(hotkey)
        self._setup_hotkey()
        if repaired_modifier_only:
            message = (
                f"Сочетание {pretty_hotkey(requested_hotkey)} состоит только из Ctrl/Shift/Alt/Win "
                f"и может срабатывать один раз, а потом переставать. "
                f"Поставлена надёжная горячая клавиша: {pretty_hotkey(hotkey)}"
            )
        else:
            message = f"Горячая клавиша установлена: {pretty_hotkey(hotkey)}"
        if show_message:
            messagebox.showinfo(APP_NAME, message)
        else:
            self.status_var.set(message)

    def capture_hotkey(self) -> None:
        if keyboard is None:
            messagebox.showerror(APP_NAME, "Не установлен keyboard. Выполни: pip install keyboard")
            return

        popup = tk.Toplevel(self.root)
        popup.title("Новое сочетание")
        popup.geometry("420x140")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        ttk.Label(
            popup,
            text="Нажми новое сочетание клавиш\nнапример Ctrl + Shift + D",
            font=("Segoe UI", 11),
            justify="center",
        ).pack(expand=True, fill=tk.BOTH, padx=20, pady=20)

        def worker() -> None:
            try:
                captured = keyboard.read_hotkey(suppress=False)
                self.root.after(0, lambda: self._finish_hotkey_capture(popup, captured))
            except Exception as exc:
                self.root.after(0, lambda: self._finish_hotkey_capture_error(popup, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_hotkey_capture(self, popup: tk.Toplevel, captured: str) -> None:
        try:
            popup.destroy()
        except Exception:
            pass
        hotkey = normalize_hotkey(captured)
        self._set_hotkey_display_without_auto_apply(hotkey)
        self.apply_hotkey_from_ui(show_message=False)
        log_info("Hotkey captured from popup", hotkey=hotkey)

    def _finish_hotkey_capture_error(self, popup: tk.Toplevel, exc: Exception) -> None:
        try:
            popup.destroy()
        except Exception:
            pass
        self._show_error("Не удалось считать сочетание клавиш", exc)

    def _is_streaming_thread_alive(self) -> bool:
        try:
            return bool(self.streaming_thread is not None and self.streaming_thread.is_alive())
        except Exception:
            return False

    def _repair_idle_recording_state(self, reason: str) -> bool:
        """Force the UI/state back to idle when no microphone recording is active.

        This is a safety net for the recurring bug where both F9 and the tray
        menu appear to stop working after a stop. If the recorder is already
        stopped, the next toggle must be allowed to start even if a stale
        streaming thread, disabled button, old finalizing flag or busy status is
        still present.
        """
        try:
            if self.recorder.is_recording:
                return False

            status = self.status_var.get()
            try:
                button_text = str(self.record_btn.cget("text"))
                button_state = str(self.record_btn.cget("state"))
            except Exception:
                button_text = ""
                button_state = ""
            stream_alive = self._is_streaming_thread_alive()
            busy_status = status in {
                "Распознаю...",
                "Обрабатываю...",
                "Обрабатываю последний фрагмент...",
                "Идёт запись...",
                "Стриминг...",
                "Стриминг: предупреждение",
            }
            needs_repair = (
                bool(self.finalizing_recording)
                or bool(self.recording_start_in_progress)
                or bool(self.pending_hotkey_start_requested)
                or self.streaming_thread is not None
                or busy_status
                or button_state == "disabled"
                or ("Остановить" in button_text)
                or ("Обрабатываю" in button_text)
            )
            if not needs_repair:
                return False

            log_warning(
                "Recording state repaired before toggle",
                reason=reason,
                status=status,
                button_text=button_text,
                button_state=button_state,
                stream_alive=stream_alive,
                finalizing=self.finalizing_recording,
                recording_start_in_progress=self.recording_start_in_progress,
                pending_hotkey_start=self.pending_hotkey_start_requested,
                session_id=self.recording_session_id,
            )
            self.log_state(
                "recording",
                "idle_state_repaired",
                reason=reason,
                previous_status=status,
                previous_button_text=button_text,
                previous_button_state=button_state,
                previous_stream_alive=stream_alive,
            )
            try:
                self.log_hotkey_trace(
                    "idle_state_repaired",
                    reason=reason,
                    previous_status=status,
                    previous_button_text=button_text,
                    previous_button_state=button_state,
                    previous_stream_alive=stream_alive,
                )
            except Exception:
                pass

            try:
                self.streaming_stop_event.set()
            except Exception:
                pass
            try:
                self.recorder.discard_frames()
            except Exception:
                pass
            try:
                if self.timer_job:
                    self.root.after_cancel(self.timer_job)
                    self.timer_job = None
            except Exception:
                pass

            self.finalizing_recording = False
            self.recording_start_in_progress = False
            self.pending_hotkey_start_requested = False
            self.pending_hotkey_start_target = None
            self.streaming_thread = None
            self.last_wav_path = None
            self.record_started_at = None
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            try:
                self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            except Exception:
                pass
            try:
                if getattr(self.notifications, "_last_kind", "") == "recording":
                    self.notifications.hide()
            except Exception:
                pass
            return True
        except Exception as exc:
            log_exception("Could not repair idle recording state", exc, reason=reason)
            return False

    def _force_stop_recording_state(self, reason: str) -> None:
        """Emergency stop used by tray/menu/hotkey when state is inconsistent."""
        try:
            log_warning("Force-stopping recording state", reason=reason, session_id=self.recording_session_id)
            self.log_state("recording", "force_stop_requested", reason=reason)
            try:
                self.recorder.stop_discard()
            except Exception:
                try:
                    self.recorder.stop_stream_keep_frames()
                except Exception:
                    pass
                try:
                    self.recorder.discard_frames()
                except Exception:
                    pass
            try:
                self._stop_realtime_streaming(wait=False)
            except Exception:
                pass
            self.finalizing_recording = False
            self.recording_start_in_progress = False
            self.pending_hotkey_start_requested = False
            self.pending_hotkey_start_target = None
            self.streaming_thread = None
            self.last_wav_path = None
            self.record_started_at = None
            if self.timer_job:
                try:
                    self.root.after_cancel(self.timer_job)
                except Exception:
                    pass
                self.timer_job = None
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            try:
                self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            except Exception:
                pass
            try:
                if getattr(self.notifications, "_last_kind", "") == "recording":
                    self.notifications.hide()
            except Exception:
                pass
        except Exception as exc:
            log_exception("Emergency force stop failed", exc, reason=reason)

    def toggle_recording(self, origin: str = "main", captured_target: Optional[PasteTarget] = None) -> None:
        self.log_state("recording", "toggle_requested", origin=origin, captured_target=self._hotkey_target_snapshot(captured_target))
        if origin == "hotkey":
            self.log_hotkey_trace(
                "toggle_requested",
                origin=origin,
                captured_target=self._hotkey_target_snapshot(captured_target),
            )

        # If no microphone stream is active, stale finalizing/busy UI must not
        # block the next start from F9 or from the tray menu.
        self._repair_idle_recording_state(f"before_toggle:{origin}")

        if self.finalizing_recording:
            self.log_state("recording", "finalizing_force_released_before_toggle", origin=origin)
            if origin == "hotkey":
                self.log_hotkey_trace(
                    "finalizing_force_released_before_toggle",
                    captured_target=self._hotkey_target_snapshot(captured_target),
                )
            self._force_stop_recording_state(f"finalizing_before_toggle:{origin}")

        if self.status_var.get() in {"Распознаю...", "Обрабатываю...", "Обрабатываю последний фрагмент..."}:
            if not self.recorder.is_recording:
                self._repair_idle_recording_state(f"busy_status_before_toggle:{origin}")
            if self.status_var.get() in {"Распознаю...", "Обрабатываю...", "Обрабатываю последний фрагмент..."}:
                self.log_state("recording", "toggle_ignored_busy_status", origin=origin)
                return
        if self.recorder.is_recording:
            self.log_state("recording", "toggle_to_stop", origin=origin)
            self.stop_recording(origin=origin)
        else:
            self.log_state("recording", "toggle_to_start", origin=origin)
            self.start_recording(origin=origin, captured_target=captured_target)

    def start_recording(self, origin: str = "main", captured_target: Optional[PasteTarget] = None) -> None:
        if origin == "hotkey":
            self.log_hotkey_trace(
                "start_recording_called",
                origin=origin,
                captured_target=self._hotkey_target_snapshot(captured_target),
            )
        if self.recording_start_in_progress:
            log_info("Recording start ignored because another start is already in progress", origin=origin)
            self.log_state("recording", "start_ignored_already_in_progress", origin=origin)
            return
        self.recording_start_in_progress = True
        started = False
        try:
            self.log_state("recording", "start_begin", origin=origin)
            self._apply_selected_microphone()
            if not self.microphone_options:
                raise RuntimeError("Микрофон не выбран: список микрофонов пустой")
            runtime_settings = self._runtime_settings_snapshot()
            self.recording_session_id += 1
            session_id = self.recording_session_id
            log_info(
                "Recording start requested",
                origin=origin,
                session_id=session_id,
                microphone=self.microphone_var.get(),
                settings=asdict(runtime_settings),
            )
            self.processing_origin = origin
            self.last_result_ready = False
            if origin == "hotkey":
                self.paste_target = captured_target or get_paste_target()
            self.clear_texts(keep_status=True)
            self.recorder.start()
            started = True
            self.record_started_at = time.time()
            self.status_var.set("Идёт запись...")
            self.record_btn.config(text="■ Остановить запись")
            self.hotkey_ignore_until = max(
                self.hotkey_ignore_until,
                time.monotonic() + HOTKEY_START_GUARD_SECONDS,
            )
            # Recreate the toast on every new recording start. Reusing a hidden
            # Toplevel after previous success/stop notifications can make the
            # user think recording did not start, even though audio and realtime
            # insertion are already working.
            self._show_recording_notification(force_recreate=True)
            self.log_state("recording", "start_success", origin=origin, session_id=session_id)
            if origin == "hotkey":
                self.log_hotkey_trace(
                    "start_recording_success",
                    origin=origin,
                    session_id=session_id,
                    record_started_at=self.record_started_at,
                    microphone=self.microphone_var.get(),
                )
            self._start_realtime_streaming_if_enabled(origin, session_id, runtime_settings)
            self._update_timer()
        except Exception as exc:
            log_exception("Could not start recording", exc, origin=origin)
            self.log_state("recording", "start_failed", origin=origin, error=str(exc))
            if origin == "hotkey":
                self.log_hotkey_trace("start_recording_failed", origin=origin, error=str(exc))
            self.notify("⚠ Не удалось начать запись", kind="error", duration_ms=3000)
            self._show_error("Не удалось начать запись", exc)
        finally:
            self.recording_start_in_progress = False
            if not started:
                self.hotkey_ignore_until = max(
                    self.hotkey_ignore_until,
                    time.monotonic() + HOTKEY_DEBOUNCE_SECONDS,
                )

    def stop_recording(self, origin: str = "main") -> None:
        if origin == "hotkey":
            self.log_hotkey_trace("stop_recording_called", origin=origin, session_id=self.recording_session_id)
        """Stop recording without any final paste.

        This build is realtime-only: confirmed chunks are inserted while recording.
        When the user presses the hotkey again, recording stops and the app does
        not transcribe/paste the whole final recording, so there are no duplicate
        blocks after dictation.
        """
        try:
            self.log_state("recording", "stop_begin", origin=origin, session_id=self.recording_session_id)
            self.hotkey_ignore_until = max(
                self.hotkey_ignore_until,
                time.monotonic() + HOTKEY_DEBOUNCE_SECONDS,
            )
            self.processing_origin = origin
            log_info("Recording stop requested", origin=origin, session_id=self.recording_session_id)
            self.recorder.stop_stream_keep_frames()
            stopped_session_id = self.recording_session_id
            finishing_thread = self.streaming_thread
            self._stop_realtime_streaming(wait=False)
            self.last_wav_path = None
            if self.timer_job:
                self.root.after_cancel(self.timer_job)
                self.timer_job = None

            # Do not block the hotkey while the old streaming worker exits.
            # The app is realtime-only: the important text is committed while
            # recording. Waiting here for a final chunk made F9 appear broken
            # after the first stop because the UI stayed in finalizing state.
            self.finalizing_recording = False
            self.pending_hotkey_start_requested = False
            self.pending_hotkey_start_target = None
            self.streaming_thread = None
            self.recorder.discard_frames()
            self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            self.log_state(
                "recording",
                "stop_released_immediately",
                origin=origin,
                session_id=stopped_session_id,
                finishing_thread_alive=bool(finishing_thread is not None and finishing_thread.is_alive()),
                final_chunk_on_stop=STREAM_FINAL_CHUNK_ON_STOP,
            )
            if origin == "hotkey":
                self.log_hotkey_trace(
                    "stop_released_immediately",
                    origin=origin,
                    session_id=stopped_session_id,
                    finishing_thread_alive=bool(finishing_thread is not None and finishing_thread.is_alive()),
                    final_chunk_on_stop=STREAM_FINAL_CHUNK_ON_STOP,
                )
            self.notify(
                "⏹ Запись остановлена\nГорячая клавиша снова доступна сразу",
                kind="success",
                duration_ms=1200,
            )
        except Exception as exc:
            log_exception("Could not stop recording", exc, origin=origin, session_id=self.recording_session_id)
            self.log_state("recording", "stop_failed", origin=origin, session_id=self.recording_session_id, error=str(exc))
            self._force_stop_recording_state(f"stop_failed:{origin}")
            self.notify("⚠ Не удалось остановить запись, состояние сброшено", kind="error", duration_ms=3000)
            self._show_error("Не удалось остановить запись", exc)

    def _get_realtime_chunk_seconds(self) -> int:
        try:
            value = int(self.realtime_chunk_seconds_var.get())
        except Exception:
            value = 4
        return max(1, min(10, value))

    def _runtime_settings_snapshot(self) -> RuntimeSettings:
        return RuntimeSettings(
            mode=self.mode_var.get(),
            language=self.language_var.get(),
            whisper_model=(
                self.whisper_model_var.get()
                if self.whisper_model_var.get() in WHISPER_MODEL_OPTIONS
                else LOCAL_WHISPER_MODEL
            ),
            recognition_quality=(
                self.recognition_quality_var.get()
                if self.recognition_quality_var.get() in QUALITY_OPTIONS
                else "Максимальная точность"
            ),
            inference_device=(
                self.inference_device_var.get()
                if self.inference_device_var.get() in INFERENCE_DEVICE_OPTIONS
                else "auto"
            ),
            compute_type=(
                self.compute_type_var.get()
                if self.compute_type_var.get() in COMPUTE_TYPE_OPTIONS
                else "auto"
            ),
            use_vad_filter=self.use_vad_filter_var.get(),
            custom_terms=self.custom_terms_var.get(),
            deep_grammar=self.deep_grammar_var.get(),
            realtime_chunk_seconds=self._get_realtime_chunk_seconds(),
            realtime_fast_quality=self.realtime_fast_quality_var.get(),
            realtime_speed_profile=(
                self.realtime_speed_profile_var.get()
                if self.realtime_speed_profile_var.get() in STREAMING_SPEED_OPTIONS
                else "Быстрее"
            ),
        )

    def _cpu_realtime_path_expected(self, runtime_settings: RuntimeSettings) -> bool:
        missing_cuda_dlls = bool(self.transcriber._windows_missing_cuda_dlls())
        if runtime_settings.inference_device == "cpu":
            return True
        if runtime_settings.inference_device == "cuda":
            return missing_cuda_dlls
        return missing_cuda_dlls

    def _streaming_model_name(self, runtime_settings: RuntimeSettings) -> str:
        model_name = runtime_settings.whisper_model
        if not self._cpu_realtime_path_expected(runtime_settings):
            return model_name
        if model_name in {"medium", "large-v3"}:
            return "small"
        if model_name == "small" and runtime_settings.realtime_speed_profile == "Быстрее":
            return "base"
        return model_name

    def _schedule_warmup_transcriber(self, delay_ms: int = 650) -> None:
        try:
            if self.transcriber_warmup_job is not None:
                self.root.after_cancel(self.transcriber_warmup_job)
        except Exception:
            pass
        self.transcriber_warmup_job = self.root.after(delay_ms, self._run_scheduled_warmup_transcriber)

    def _run_scheduled_warmup_transcriber(self) -> None:
        self.transcriber_warmup_job = None
        self._warmup_transcriber_async()

    def _transcriber_settings_changed(self, _event: object = None) -> None:
        self._save_settings()
        self._schedule_warmup_transcriber(650)

    def _warmup_transcriber_async(self) -> None:
        try:
            runtime_settings = self._runtime_settings_snapshot()
            streaming_model_name = self._streaming_model_name(runtime_settings)
            warm_device = "cpu" if self._cpu_realtime_path_expected(runtime_settings) else runtime_settings.inference_device
            warm_compute = runtime_settings.compute_type
            if warm_device == "cpu" and warm_compute not in {"int8", "float32"}:
                warm_compute = "int8"
        except Exception as exc:
            log_exception("Could not build transcriber warmup settings", exc)
            return

        def worker() -> None:
            try:
                log_info(
                    "Transcriber warmup started",
                    selected_model=runtime_settings.whisper_model,
                    streaming_model=streaming_model_name,
                    device=warm_device,
                    compute_type=warm_compute,
                )
                self.transcriber._load_model(streaming_model_name, device=warm_device, compute_type=warm_compute)
                log_info(
                    "Transcriber warmup finished",
                    streaming_model=streaming_model_name,
                    backend=self.transcriber.active_backend_label,
                )
            except BaseException as exc:
                log_exception(
                    "Transcriber warmup failed",
                    exc,
                    streaming_model=streaming_model_name,
                    device=warm_device,
                    compute_type=warm_compute,
                )

        threading.Thread(target=worker, name="voiceflow-transcriber-warmup", daemon=True).start()

    def _start_realtime_streaming_if_enabled(
        self,
        origin: str,
        session_id: int,
        runtime_settings: RuntimeSettings,
    ) -> None:
        # Realtime-only behavior. Hotkey recording pastes fragments into whatever
        # input field is focused at the moment each fragment is ready. This lets
        # the user move the caret between windows while one dictation session is running.
        # Main-window recording can only preview because it has no external caret.
        mode = "Вставлять фрагментами"
        self.realtime_streaming_mode_var.set(mode)
        if origin != "hotkey":
            mode = "Только превью"

        streaming_model_name = self._streaming_model_name(runtime_settings)
        if streaming_model_name != runtime_settings.whisper_model:
            log_info(
                "Realtime model optimized for faster CPU streaming",
                selected_model=runtime_settings.whisper_model,
                streaming_model=streaming_model_name,
                inference_device=runtime_settings.inference_device,
                speed_profile=runtime_settings.realtime_speed_profile,
            )

        self.streaming_stop_event = threading.Event()
        self.stream_context_reset_event.clear()
        self.stream_last_frame_index = self.recorder.frames_count()
        self.stream_inserted_any = False
        self.stream_inserted_text = ""
        self.stream_preview_raw_text = ""
        self.stream_preview_clean_text = ""
        self.streaming_thread = threading.Thread(
            target=self._realtime_stream_worker,
            args=(origin, mode, self.streaming_stop_event, session_id, runtime_settings),
            daemon=True,
        )
        self.streaming_thread.start()
        log_info(
            "Realtime streaming worker started",
            origin=origin,
            mode=mode,
            session_id=session_id,
            realtime_chunk_seconds=runtime_settings.realtime_chunk_seconds,
            realtime_speed_profile=runtime_settings.realtime_speed_profile,
            streaming_model=streaming_model_name,
        )
        self.log_state(
            "streaming",
            "streaming_worker_started",
            origin=origin,
            mode=mode,
            session_id=session_id,
            realtime_chunk_seconds=runtime_settings.realtime_chunk_seconds,
            realtime_speed_profile=runtime_settings.realtime_speed_profile,
            streaming_model=streaming_model_name,
        )

    def _stop_realtime_streaming(self, wait: bool = False) -> None:
        try:
            self.streaming_stop_event.set()
        except Exception:
            pass
        if wait and self.streaming_thread is not None and self.streaming_thread.is_alive():
            self.status_var.set("Обрабатываю последний фрагмент...")
            log_info("Waiting for realtime streaming worker to finish", session_id=self.recording_session_id)
            self.streaming_thread.join()
            self.streaming_thread = None

    def _finish_streaming_after_stop_async(
        self,
        session_id: int,
        finishing_thread: Optional[threading.Thread],
    ) -> None:
        def waiter() -> None:
            finish_message_sent = False
            try:
                if finishing_thread is not None and finishing_thread.is_alive():
                    log_info("Waiting for realtime streaming worker in background", session_id=session_id)
                    finishing_thread.join(timeout=STREAM_FINISH_TIMEOUT_SECONDS)
                    if finishing_thread.is_alive():
                        log_warning(
                            "Realtime streaming worker finish timed out; releasing UI",
                            session_id=session_id,
                            timeout_seconds=STREAM_FINISH_TIMEOUT_SECONDS,
                        )
                        self.worker_queue.put(("stream_finish_timeout", session_id))
                        finish_message_sent = True
                        return
            except BaseException as exc:
                log_exception("Background wait for realtime worker failed", exc, session_id=session_id)
            finally:
                if not finish_message_sent:
                    self.worker_queue.put(("stream_finished", session_id))

        threading.Thread(target=waiter, name="voiceflow-stream-finalizer", daemon=True).start()

    def _frames_to_float_mono(self, frames: list[np.ndarray]) -> np.ndarray:
        if not frames:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(frames, axis=0)
        if audio.ndim > 1:
            audio = audio.astype(np.float32).mean(axis=1)
        else:
            audio = audio.astype(np.float32).reshape(-1)
        if audio.size and np.nanmax(np.abs(audio)) > 2.0:
            audio = audio / 32768.0
        if audio.size:
            audio = audio - float(np.mean(audio))
        return audio

    def _stream_audio_stats(self, frames: list[np.ndarray], sample_rate: int) -> dict[str, float]:
        audio = self._frames_to_float_mono(frames)
        if audio.size == 0 or sample_rate <= 0:
            return {"duration": 0.0, "rms": 0.0, "peak": 0.0, "trailing_silence": 0.0}
        duration = float(audio.size) / float(sample_rate)
        abs_audio = np.abs(audio)
        peak = float(np.max(abs_audio)) if abs_audio.size else 0.0
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        threshold = max(0.006, min(0.028, rms * 0.72))
        active = np.where(abs_audio > threshold)[0]
        active_ratio = float(active.size) / float(audio.size) if audio.size else 0.0
        peak_to_rms = peak / max(rms, 1e-9)
        trailing_silence = duration
        if active.size:
            trailing_silence = float(audio.size - int(active[-1]) - 1) / float(sample_rate)

        # A second, adaptive pause detector for noisy rooms. The old detector
        # used an absolute-ish threshold and often saw PC fan / air purifier
        # noise as continuous activity, so trailing_silence stayed near 0 even
        # after the user paused for a sentence. Here we measure short-window
        # RMS, estimate the noise floor, and detect when speech-level energy
        # stopped while background noise continues.
        speech_trailing_silence = trailing_silence
        speech_threshold = threshold
        noise_floor_rms = 0.0
        speech_high_rms = 0.0
        try:
            window = max(1, int(sample_rate * 0.06))  # 60 ms windows
            usable_size = (audio.size // window) * window
            if usable_size >= window * 4:
                windowed = audio[:usable_size].reshape(-1, window)
                win_rms = np.sqrt(np.mean(windowed * windowed, axis=1))
                if win_rms.size:
                    noise_floor_rms = float(np.percentile(win_rms, 20))
                    speech_high_rms = float(np.percentile(win_rms, 85))
                    median_rms = float(np.median(win_rms))
                    speech_threshold = max(
                        0.006,
                        min(0.075, max(noise_floor_rms * 2.1, median_rms * 1.25, speech_high_rms * 0.32)),
                    )
                    speech_windows = np.where(win_rms > speech_threshold)[0]
                    if speech_windows.size:
                        silent_windows = int(win_rms.size - int(speech_windows[-1]) - 1)
                        speech_trailing_silence = max(0.0, silent_windows * window / float(sample_rate))
                    else:
                        speech_trailing_silence = duration
        except Exception:
            speech_trailing_silence = trailing_silence

        pause_seconds = max(trailing_silence, speech_trailing_silence)
        return {
            "duration": duration,
            "rms": rms,
            "peak": peak,
            "peak_to_rms": peak_to_rms,
            "active_ratio": active_ratio,
            "trailing_silence": trailing_silence,
            "speech_trailing_silence": speech_trailing_silence,
            "pause_seconds": pause_seconds,
            "speech_threshold": speech_threshold,
            "noise_floor_rms": noise_floor_rms,
            "speech_high_rms": speech_high_rms,
        }

    def _is_probably_speech(self, frames: list[np.ndarray], sample_rate: int) -> bool:
        stats = self._stream_audio_stats(frames, sample_rate)
        return stats["duration"] >= 0.35 and stats["peak"] >= 0.010 and stats["rms"] >= 0.0025

    def _stream_quality(self, runtime_settings: RuntimeSettings) -> str:
        profile = runtime_settings.realtime_speed_profile
        if runtime_settings.realtime_fast_quality or profile == "Быстрее":
            return "Быстро"
        if profile == "Баланс":
            return "Точно"
        if self._cpu_realtime_path_expected(runtime_settings):
            return "Точно"
        return runtime_settings.recognition_quality

    def _normalize_stream_words(self, text: str) -> list[str]:
        cleaned = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", " ", text.lower())
        return [w for w in cleaned.split() if w]

    def _dedupe_stream_chunk(self, previous_text: str, new_text: str) -> str:
        """Remove repeated prefix from a stream chunk using token overlap.

        Voice-control commands are allowed to repeat. Without this exception,
        a command like "новая строка" can work once and then be swallowed as
        an already-seen duplicate later in the same dictation session.
        """
        new_text = re.sub(r"\s+", " ", new_text or "").strip()
        previous_text = re.sub(r"\s+", " ", previous_text or "").strip()
        if not new_text:
            return ""
        if voice_control_command_from_text(new_text):
            return new_text
        if not previous_text:
            return new_text
        prev_words = self._normalize_stream_words(previous_text)
        new_words = self._normalize_stream_words(new_text)
        if not prev_words or not new_words:
            return new_text
        max_overlap = min(14, len(prev_words), len(new_words))
        best = 0
        for size in range(max_overlap, 0, -1):
            if prev_words[-size:] == new_words[:size]:
                best = size
                break
        if best <= 0:
            normalized_prev = " ".join(prev_words)
            normalized_new = " ".join(new_words)
            if normalized_new and normalized_new in normalized_prev:
                return ""
            return new_text
        original_tokens = new_text.split()
        if best >= len(original_tokens):
            return ""
        return " ".join(original_tokens[best:]).strip()

    def _is_bad_stream_text(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not normalized or len(normalized) < 2:
            return True
        punctuation_only = re.sub(r"\s+", "", normalized)
        # faster-whisper can hallucinate chunks like ".. .." or "..." during
        # silence/noise. They were cleaned into "..." / "....." and inserted
        # into the target field. Keep spoken punctuation commands as words
        # ("точка", "знак вопроса", "знак внимания") but reject punctuation-only chunks.
        if re.fullmatch(r"[\.。…]+", punctuation_only) or re.fullmatch(r"[\.,;:!?…\-—]+", punctuation_only):
            return True
        bad_phrases = [
            "спасибо за просмотр", "спасибо за внимание", "продолжение следует",
            "субтитры сделал", "субтитры создал", "субтитры создала", "субтитры создавал",
            "субтитры подготовил", "редактор субтитров", "подписывайтесь", "смотрите далее",
            "thank you for watching", "subtitles by", "captioned by", "dima torzok", "dimatorzok",
            "дима торжок", "диматорзок", "субтитры dima", "субтитры dimatorzok",
        ]
        if any(phrase in normalized for phrase in bad_phrases):
            return True
        words = normalized.split()
        if len(words) >= 4 and len(set(words)) <= 2:
            return True
        return False

    def _soften_open_stream_text(self, text: str) -> str:
        text = re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()
        if not text:
            return ""
        # If a realtime chunk was cut mid-phrase, punctuation at the end is
        # usually Whisper/cleanup noise. The old code removed only one dot,
        # producing broken tails like "Сейчас.." and "с.?". Remove the whole
        # dangling punctuation run so the next chunk can continue the sentence.
        if not re.search(r"(?i)\b(?:т\.д|т\.п|и т\.д|и т\.п)\.$", text):
            text = re.sub(r"(?:\s*[.!?…]+)+\s*$", "", text).rstrip()
            text = re.sub(r"\s+[,;:]\s*$", "", text).rstrip()
        return text

    def _clean_stream_chunk_for_commit(self, raw_text: str, runtime_settings: RuntimeSettings, keep_sentence_end: bool = True) -> tuple[str, str]:
        raw_text = re.sub(r"\s+", " ", raw_text or "").strip()
        raw_had_trailing_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text))
        if self._is_bad_stream_text(raw_text):
            return "", ""
        clean_mode = "Чистый текст" if runtime_settings.mode != "Точно как сказано" else "Точно как сказано"
        cleaned = self.cleaner.clean(
            raw_text,
            clean_mode,
            runtime_settings.language,
            custom_terms=runtime_settings.custom_terms,
            deep_grammar=False,
        )
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned).strip()
        if self._is_bad_stream_text(cleaned):
            # Do not resurrect punctuation-only hallucinations by falling back
            # to raw_text. If both forms are unusable, skip the chunk.
            if self._is_bad_stream_text(raw_text):
                return "", ""
            cleaned = raw_text
        if raw_had_trailing_ellipsis or not keep_sentence_end:
            cleaned = self._soften_open_stream_text(cleaned)
        return raw_text, cleaned

    def _stream_has_sentence_end(self, text: str) -> bool:
        return bool(re.search(r"[.!?…][\"'»)\]]*$", (text or "").strip()))

    def _lowercase_continuation_start(self, text: str) -> str:
        if not text:
            return text
        proper_starts = {
            "ChatGPT", "OpenAI", "Telegram", "WhatsApp", "Gmail", "Google", "Python",
            "JavaScript", "TypeScript", "PowerShell", "Windows", "Whisper", "CUDA",
        }
        first_word = re.match(r"^[\"'«(]*([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_+-]*)", text)
        if first_word and first_word.group(1) in proper_starts:
            return text
        return re.sub(
            r"^([\"'«(]*)([А-ЯЁA-Z])([а-яёa-z])",
            lambda m: m.group(1) + m.group(2).lower() + m.group(3),
            text,
            count=1,
        )

    def _prepare_stream_chunk_for_paste(
        self,
        previous_text: str,
        chunk_text: str,
        commit_meta: Optional[dict[str, object]] = None,
        raw_text: str = "",
    ) -> str:
        chunk_text = re.sub(r"[ \t\r\f\v]+", " ", chunk_text or "").strip()
        if not chunk_text:
            return ""

        meta = commit_meta or {}
        sentence_pause = bool(meta.get("sentence_pause"))
        pause_seconds = float(meta.get("pause_seconds") or 0.0)
        whisper_sentence_end = bool(meta.get("whisper_sentence_end"))
        raw_text = raw_text or str(meta.get("raw_text") or "")

        # Preserve/add final punctuation when we have a real reason for it:
        # 1) the user made a clear pause in speech; 2) Whisper confidently ended
        # the phrase with .?! . This fixes logs where raw text had periods but
        # cleaned realtime text inserted every phrase without a dot.
        raw_end_match = re.search(r"([.!?])(?:[\"'»\)\]]*)\s*$", raw_text.strip())
        if raw_end_match and not re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text.strip()):
            whisper_sentence_end = True

        if sentence_pause and not self._stream_has_sentence_end(chunk_text):
            chunk_text = re.sub(r"[,;:]\s*$", "", chunk_text).rstrip() + "."
        elif whisper_sentence_end and not self._stream_has_sentence_end(chunk_text):
            end_char = raw_end_match.group(1) if raw_end_match else "."
            chunk_text = re.sub(r"[,;:]\s*$", "", chunk_text).rstrip() + end_char
        elif not sentence_pause and not whisper_sentence_end and meta.get("forced_commit"):
            # Forced max-duration chunks can be cut mid-sentence. Do not invent
            # a dot unless there was a pause or Whisper itself ended the phrase.
            chunk_text = self._soften_open_stream_text(chunk_text)

        if previous_text and not self._stream_has_sentence_end(previous_text):
            # If the previous inserted chunk did not end as a sentence, this is
            # a continuation unless the current chunk was explicitly punctuated
            # by a strong pause/Whisper.
            if not sentence_pause:
                chunk_text = self._lowercase_continuation_start(chunk_text)
        log_category(
            "streaming",
            "pause_punctuation_decision",
            chunk_preview=chunk_text[:160],
            pause_seconds=round(pause_seconds, 3),
            sentence_pause=sentence_pause,
            whisper_sentence_end=whisper_sentence_end,
            previous_had_sentence_end=self._stream_has_sentence_end(previous_text),
            forced_commit=bool(meta.get("forced_commit")),
        )
        return chunk_text

    def _get_missing_final_tail(self, already_inserted: str, final_text: str) -> str:
        """Return only the not-yet-inserted final tail after realtime paste."""
        already_words = self._normalize_stream_words(already_inserted)
        final_words = self._normalize_stream_words(final_text)
        if not final_words:
            return ""
        if not already_words:
            return final_text.strip()
        max_overlap = min(len(already_words), len(final_words), 28)
        best = 0
        for size in range(max_overlap, 1, -1):
            suffix = already_words[-size:]
            for start_pos in range(0, min(10, max(1, len(final_words) - size + 1))):
                if final_words[start_pos:start_pos + size] == suffix:
                    best = start_pos + size
                    break
            if best:
                break
        if best <= 0:
            return ""
        original_tokens = final_text.split()
        if best >= len(original_tokens):
            return ""
        return " ".join(original_tokens[best:]).strip()

    def _realtime_stream_worker(
        self,
        origin: str,
        mode: str,
        stop_event: threading.Event,
        session_id: int,
        runtime_settings: RuntimeSettings,
    ) -> None:
        """Stable phrase-based realtime recognition.

        This does not paste every tiny audio chunk. Short chunks are the main
        reason for wrong text, hallucinations and duplicated fragments in local
        Whisper. We accumulate audio and commit only after a pause or when the
        chunk becomes long enough.
        """
        configured_interval = runtime_settings.realtime_chunk_seconds
        profile = runtime_settings.realtime_speed_profile
        streaming_model_name = self._streaming_model_name(runtime_settings)
        if profile == "Быстрее":
            poll_interval = 0.18
            min_seconds = min(max(0.75, configured_interval * 0.35), 1.35)
            max_seconds = min(max(1.60, configured_interval * 0.75), 2.80)
            trailing_silence_required = 0.24
            sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_FAST
        elif profile == "Баланс":
            poll_interval = 0.26
            min_seconds = min(max(1.35, configured_interval * 0.55), 2.40)
            max_seconds = min(max(3.80, configured_interval * 1.35), 6.00)
            trailing_silence_required = 0.42
            sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_BALANCE
        else:
            # Noisy/distant microphones need longer phrase chunks. Logs showed
            # profile "Качество" with 4-second forced chunks and almost no
            # trailing silence; Whisper hallucinated subtitles/outros and those
            # chunks were discarded, so real words could be lost. In quality mode
            # wait longer and prefer a real pause before committing.
            if self._cpu_realtime_path_expected(runtime_settings):
                poll_interval = 0.32
                min_seconds = min(max(1.80, configured_interval * 0.60), 2.80)
                max_seconds = min(max(4.80, configured_interval * 1.25), 7.00)
                trailing_silence_required = 0.55
                sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_QUALITY
            else:
                poll_interval = 0.24
                min_seconds = min(max(1.60, configured_interval * 0.55), 2.60)
                max_seconds = min(max(4.20, configured_interval * 1.15), 6.50)
                trailing_silence_required = 0.48
                sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_QUALITY
        streaming_vad_filter = bool(runtime_settings.use_vad_filter or profile in {"Баланс", "Качество"})
        bad_retry_after_frame_index = 0
        bad_filtered_streak = 0
        max_bad_hold_seconds = max(max_seconds + 3.0, max_seconds * 1.75)
        committed_raw_context = ""
        committed_clean_context = ""
        last_frame_index = self.stream_last_frame_index
        log_info(
            "Realtime timing configured",
            session_id=session_id,
            profile=profile,
            model=streaming_model_name,
            poll_interval=poll_interval,
            min_seconds=round(min_seconds, 2),
            max_seconds=round(max_seconds, 2),
            trailing_silence_required=round(trailing_silence_required, 2),
            sentence_end_pause_seconds=round(sentence_end_pause_seconds, 2),
            streaming_vad_filter=streaming_vad_filter,
            max_bad_hold_seconds=round(max_bad_hold_seconds, 2),
        )

        def reset_context_if_requested() -> None:
            nonlocal committed_raw_context, committed_clean_context, last_frame_index
            if self.stream_context_reset_event.is_set():
                committed_raw_context = ""
                committed_clean_context = ""
                self.stream_context_reset_event.clear()
                log_info("Realtime dictation context reset", session_id=session_id)

        def commit_frames(frames: list[np.ndarray], new_index: int, sample_rate: int, is_final: bool = False) -> None:
            nonlocal committed_raw_context, committed_clean_context, last_frame_index, bad_retry_after_frame_index, bad_filtered_streak
            reset_context_if_requested()
            if not frames:
                last_frame_index = new_index
                return

            stats = self._stream_audio_stats(frames, sample_rate)
            duration = stats["duration"]
            has_pause = False
            forced_commit = False
            pause_seconds = float(stats.get("pause_seconds", stats.get("trailing_silence", 0.0)))
            sentence_pause = False
            if is_final:
                if duration < 0.20 or stats["peak"] < 0.008 or stats["rms"] < 0.0018:
                    last_frame_index = new_index
                    return
                has_pause = True
                sentence_pause = True
            else:
                if duration < min_seconds:
                    return
                if not self._is_probably_speech(frames, sample_rate):
                    last_frame_index = new_index
                    return
                pause_seconds = float(stats.get("pause_seconds", stats.get("trailing_silence", 0.0)))
                sentence_pause = pause_seconds >= sentence_end_pause_seconds
                has_pause = pause_seconds >= trailing_silence_required
                forced_commit = duration >= max_seconds
                if not has_pause and not forced_commit:
                    return
                log_info(
                    "Realtime commit triggered",
                    session_id=session_id,
                    duration=round(duration, 3),
                    trailing_silence=round(stats["trailing_silence"], 3),
                    speech_trailing_silence=round(stats.get("speech_trailing_silence", 0.0), 3),
                    pause_seconds=round(pause_seconds, 3),
                    sentence_pause=sentence_pause,
                    sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                    forced_commit=forced_commit,
                    has_pause=has_pause,
                    frame_index=new_index,
                    peak=round(stats.get("peak", 0.0), 5),
                    rms=round(stats.get("rms", 0.0), 5),
                    peak_to_rms=round(stats.get("peak_to_rms", 0.0), 3),
                    active_ratio=round(stats.get("active_ratio", 0.0), 3),
                    streaming_vad_filter=streaming_vad_filter,
                )

            # Never freeze realtime input after a bad/noisy chunk.
            # Older builds tried to retain audio after Whisper returned an empty
            # or hallucinated result, but mixed sample counts with callback-frame
            # indexes. That made retry_after_frame_index enormous, so after one
            # inserted sentence the worker kept delaying the same growing chunk
            # forever and no more text was pasted. Keep dictation continuous:
            # bad chunks are logged and skipped below, then the stream advances.

            wav_path = self.recorder.frames_to_wav(frames, prefix="stream_stable_chunk")
            if wav_path is None:
                last_frame_index = new_index
                return
            try:
                transcribed_raw = self.transcriber.transcribe(
                    wav_path,
                    runtime_settings.language,
                    model_name=streaming_model_name,
                    quality=self._stream_quality(runtime_settings),
                    custom_terms=runtime_settings.custom_terms,
                    use_vad_filter=streaming_vad_filter,
                    context_text=committed_raw_context[-500:],
                    device=runtime_settings.inference_device,
                    compute_type=runtime_settings.compute_type,
                    streaming=True,
                )
                raw = transcribed_raw
                raw_text_for_end = (raw or "").strip()
                raw_had_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text_for_end))
                whisper_sentence_end = bool(re.search(r"[.!?][\"'»\)\]]*$", raw_text_for_end)) and not raw_had_ellipsis
                keep_sentence_end = bool(is_final or sentence_pause or whisper_sentence_end)
                raw, cleaned = self._clean_stream_chunk_for_commit(
                    raw,
                    runtime_settings,
                    keep_sentence_end=keep_sentence_end,
                )
                if not raw and not cleaned:
                    bad_filtered_streak += 1
                    # Do not retain bad chunks for retry. Retaining used to
                    # make last_frame_index stay old; after one empty/noisy
                    # transcription the next chunk grew to minutes and realtime
                    # insertion effectively stopped. A filtered chunk means:
                    # skip it, advance the stream, and keep listening.
                    retain_audio_for_retry = False
                    bad_retry_after_frame_index = 0
                    log_dictation_text(
                        "stream_filtered",
                        session_id=session_id,
                        origin=origin,
                        mode=mode,
                        raw_text=transcribed_raw,
                        reason="bad_or_empty_stream_text",
                        model=streaming_model_name,
                        language=runtime_settings.language,
                        device=runtime_settings.inference_device,
                        compute_type=runtime_settings.compute_type,
                        speed_profile=runtime_settings.realtime_speed_profile,
                        is_final=is_final,
                        has_pause=has_pause,
                        forced_commit=forced_commit,
                        audio_duration=round(duration, 3),
                        trailing_silence=round(stats["trailing_silence"], 3),
                        speech_trailing_silence=round(stats.get("speech_trailing_silence", 0.0), 3),
                        pause_seconds=round(pause_seconds, 3),
                        sentence_pause=bool(sentence_pause),
                        sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                        whisper_sentence_end=bool(whisper_sentence_end),
                        keep_sentence_end=bool(keep_sentence_end),
                        peak=round(stats.get("peak", 0.0), 5),
                        rms=round(stats.get("rms", 0.0), 5),
                        peak_to_rms=round(stats.get("peak_to_rms", 0.0), 3),
                        active_ratio=round(stats.get("active_ratio", 0.0), 3),
                        streaming_vad_filter=streaming_vad_filter,
                        bad_filtered_streak=bad_filtered_streak,
                        retain_audio_for_retry=retain_audio_for_retry,
                        retry_after_frame_index=bad_retry_after_frame_index if retain_audio_for_retry else None,
                    )
                    log_info(
                        "Realtime chunk filtered as hallucination/noise",
                        session_id=session_id,
                        raw_text=transcribed_raw,
                        retain_audio_for_retry=retain_audio_for_retry,
                        bad_filtered_streak=bad_filtered_streak,
                        duration=round(duration, 3),
                        peak=round(stats.get("peak", 0.0), 5),
                        rms=round(stats.get("rms", 0.0), 5),
                        active_ratio=round(stats.get("active_ratio", 0.0), 3),
                    )
                    if retain_audio_for_retry:
                        return
                    last_frame_index = new_index
                    return
                raw_delta = self._dedupe_stream_chunk(committed_raw_context, raw)
                clean_delta = self._dedupe_stream_chunk(committed_clean_context, cleaned)
                if raw_delta or clean_delta:
                    if not raw_delta:
                        raw_delta = clean_delta
                    if not clean_delta:
                        clean_delta = raw_delta
                    raw_context_delta, raw_context_command = split_trailing_voice_control_command(raw_delta)
                    clean_context_delta, clean_context_command = split_trailing_voice_control_command(clean_delta)
                    if raw_context_command is None:
                        raw_context_delta = raw_delta
                    if clean_context_command is None:
                        clean_context_delta = clean_delta
                    if raw_context_delta:
                        committed_raw_context = (committed_raw_context + " " + raw_context_delta).strip()
                    if clean_context_delta:
                        committed_clean_context = (committed_clean_context + " " + clean_context_delta).strip()
                    commit_meta = {
                        "pause_seconds": pause_seconds,
                        "trailing_silence": float(stats.get("trailing_silence", 0.0)),
                        "speech_trailing_silence": float(stats.get("speech_trailing_silence", 0.0)),
                        "sentence_pause": bool(sentence_pause),
                        "sentence_end_pause_seconds": float(sentence_end_pause_seconds),
                        "has_pause": bool(has_pause),
                        "forced_commit": bool(forced_commit),
                        "whisper_sentence_end": bool(whisper_sentence_end),
                        "keep_sentence_end": bool(keep_sentence_end),
                        "raw_text": raw_delta,
                    }
                    log_dictation_text(
                        "stream_chunk",
                        session_id=session_id,
                        origin=origin,
                        mode=mode,
                        raw_text=raw_delta,
                        cleaned_text=clean_delta,
                        full_clean_context=committed_clean_context[-1200:],
                        model=streaming_model_name,
                        language=runtime_settings.language,
                        device=runtime_settings.inference_device,
                        compute_type=runtime_settings.compute_type,
                        speed_profile=runtime_settings.realtime_speed_profile,
                        is_final=is_final,
                        has_pause=has_pause,
                        forced_commit=forced_commit,
                        audio_duration=round(duration, 3),
                        trailing_silence=round(stats["trailing_silence"], 3),
                        speech_trailing_silence=round(stats.get("speech_trailing_silence", 0.0), 3),
                        pause_seconds=round(pause_seconds, 3),
                        sentence_pause=bool(sentence_pause),
                        sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                        whisper_sentence_end=bool(whisper_sentence_end),
                        keep_sentence_end=bool(keep_sentence_end),
                        peak=round(stats.get("peak", 0.0), 5),
                        rms=round(stats.get("rms", 0.0), 5),
                        peak_to_rms=round(stats.get("peak_to_rms", 0.0), 3),
                        active_ratio=round(stats.get("active_ratio", 0.0), 3),
                        streaming_vad_filter=streaming_vad_filter,
                    )
                    bad_filtered_streak = 0
                    bad_retry_after_frame_index = 0
                    self.worker_queue.put(("stream_result", (session_id, raw_delta, clean_delta, origin, mode, is_final, commit_meta)))
                last_frame_index = new_index
            finally:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass

        while not stop_event.wait(poll_interval):
            if not self.recorder.is_recording:
                break
            try:
                reset_context_if_requested()
                frames, new_index, sample_rate = self.recorder.get_frames_since(last_frame_index)
                commit_frames(frames, new_index, sample_rate, is_final=False)
            except BaseException as exc:
                log_exception("Realtime streaming chunk failed", exc, session_id=session_id, origin=origin, mode=mode)
                try:
                    self.worker_queue.put(("stream_warning", (session_id, exc)))
                except Exception:
                    pass
                time.sleep(0.8)

        if stop_event.is_set() and not STREAM_FINAL_CHUNK_ON_STOP:
            log_info(
                "Realtime final chunk skipped to keep hotkey responsive",
                session_id=session_id,
                origin=origin,
                mode=mode,
            )
            log_dictation_text(
                "stream_final_skipped",
                session_id=session_id,
                origin=origin,
                mode=mode,
                reason="stop_should_not_block_next_hotkey",
            )
            log_info("Realtime streaming worker finished", session_id=session_id, origin=origin, mode=mode)
            return

        try:
            frames, new_index, sample_rate = self.recorder.get_frames_since(last_frame_index)
            commit_frames(frames, new_index, sample_rate, is_final=True)
        except BaseException as exc:
            log_exception("Realtime final chunk failed", exc, session_id=session_id, origin=origin, mode=mode)
            try:
                self.worker_queue.put(("stream_warning", (session_id, exc)))
            except Exception:
                pass
        log_info("Realtime streaming worker finished", session_id=session_id, origin=origin, mode=mode)

    def _append_stream_text(self, widget: tk.Text, text: str) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", (text or "").strip())
        if not text:
            return
        current = widget.get("1.0", tk.END).strip()
        if current:
            separator = "" if text.startswith("\n") else " "
            widget.insert(tk.END, separator + text)
        else:
            widget.insert(tk.END, text)
        widget.see(tk.END)

    def _update_timer(self) -> None:
        if not self.recorder.is_recording or self.record_started_at is None:
            return
        elapsed = int(time.time() - self.record_started_at)
        minutes, seconds = divmod(elapsed, 60)
        current = f"{minutes:02d}:{seconds:02d}"
        self.timer_var.set(current)
        # Update persistent recording notification once per second without creating a new window.
        if elapsed % 1 == 0:
            self._show_recording_notification(force_recreate=False)
        self.timer_job = self.root.after(1000, self._update_timer)

    def _process_audio_worker(self, wav_path: Path, origin: str, runtime_settings: RuntimeSettings) -> None:
        try:
            raw = self.transcriber.transcribe(
                wav_path,
                runtime_settings.language,
                model_name=runtime_settings.whisper_model,
                quality=runtime_settings.recognition_quality,
                custom_terms=runtime_settings.custom_terms,
                use_vad_filter=runtime_settings.use_vad_filter,
                device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                streaming=False,
            )
            cleaned = self.cleaner.clean(
                raw,
                runtime_settings.mode,
                runtime_settings.language,
                custom_terms=runtime_settings.custom_terms,
                deep_grammar=runtime_settings.deep_grammar,
            )
            log_dictation_text(
                "final_result",
                session_id=getattr(self, "recording_session_id", None),
                origin=origin,
                mode=runtime_settings.mode,
                raw_text=raw,
                cleaned_text=cleaned,
                model=runtime_settings.whisper_model,
                language=runtime_settings.language,
                device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                deep_grammar=runtime_settings.deep_grammar,
            )
            if self.privacy_var.get():
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
            self.worker_queue.put(("result", (raw, cleaned, origin)))
        except BaseException as exc:
            log_exception("Audio processing worker crashed", exc, wav_path=wav_path, origin=origin)
            self.worker_queue.put(("error", exc))

    def _split_stream_voice_command(self, raw: str, cleaned: str) -> tuple[str, str, Optional[dict[str, object]]]:
        raw_before, raw_command = split_trailing_voice_control_command(raw)
        cleaned_before, cleaned_command = split_trailing_voice_control_command(cleaned)
        command = cleaned_command or raw_command
        if command is None:
            return raw, cleaned, None
        if raw_command is None and normalize_voice_command_text(raw) == normalize_voice_command_text(cleaned):
            raw_before = cleaned_before
        if cleaned_command is None:
            cleaned_before = raw_before
        return raw_before.strip(), cleaned_before.strip(), command

    def _reset_stream_message_state(self, session_id: Optional[int], reason: str) -> None:
        self.stream_inserted_text = ""
        self.stream_context_reset_event.set()
        log_info("Realtime message state reset", session_id=session_id, reason=reason)

    def _handle_stream_text_piece(
        self,
        *,
        session_id: int,
        raw: str,
        cleaned: str,
        origin: str,
        stream_mode: str,
        is_final: bool,
        commit_meta: Optional[dict[str, object]] = None,
    ) -> None:
        raw = (raw or "").strip()
        cleaned = (cleaned or "").strip()
        if not raw and not cleaned:
            return
        if not raw:
            raw = cleaned
        if not cleaned:
            cleaned = raw

        self._append_stream_text(self.raw_text, raw)
        self._append_stream_text(self.clean_text, cleaned)
        self.status_var.set("Готово" if is_final and not self.recorder.is_recording else "Стриминг...")

        if stream_mode == "Вставлять фрагментами" and origin == "hotkey":
            chunk_text = cleaned if self.insert_edited_text_var.get() else raw
            chunk_text = self._dedupe_stream_chunk(self.stream_inserted_text, chunk_text).strip()
            chunk_text = self._prepare_stream_chunk_for_paste(
                self.stream_inserted_text,
                chunk_text,
                commit_meta=commit_meta,
                raw_text=raw,
            )
            if chunk_text:
                ok = self.paste_text_to_current_target(chunk_text + " ", show_messages=False)
                log_dictation_text(
                    "stream_insert",
                    session_id=session_id,
                    origin=origin,
                    mode=stream_mode,
                    raw_text=raw,
                    cleaned_text=cleaned,
                    inserted_text=chunk_text,
                    is_final=is_final,
                    paste_ok=ok,
                    commit_meta=commit_meta or {},
                )
                if ok:
                    self.stream_inserted_any = True
                    self.stream_inserted_text = (self.stream_inserted_text + " " + chunk_text).strip()
                    if self.recorder.is_recording:
                        # Do not replace the persistent "Идёт запись" toast with
                        # short success popups for every inserted chunk. Logs
                        # showed recording and typing were working, but the user
                        # thought repeat hotkey was broken because the visible
                        # toast was not the recording one.
                        self._show_recording_notification(force_recreate=False)
                    else:
                        message = "✅ Финальный фрагмент вставлен" if is_final else "⚡ Стабильный фрагмент вставлен"
                        self.notify(message, kind="success", duration_ms=1200 if is_final else 900)
                else:
                    self.notify("⚠ Фрагмент распознан, но не вставился", kind="warning", duration_ms=1600)

    def _execute_voice_control_command(
        self,
        command: dict[str, object],
        *,
        session_id: Optional[int],
        raw_text: str,
        cleaned_text: str,
        origin: str,
    ) -> bool:
        kind = str(command.get("kind", ""))
        value = command.get("value")
        label = str(command.get("label", command.get("phrase", "voice command")))
        phrase = str(command.get("phrase", ""))
        ok = False

        if kind == "text":
            ok = self.paste_text_to_current_target(str(value or ""), show_messages=False)
        else:
            current_target = get_paste_target()
            if pyautogui is not None:
                try:
                    # Voice control hotkeys/keys should also act in the current
                    # field under the cursor, not in the window where recording started.
                    time.sleep(0.025)

                    def run_action(action_kind: str, action_value: object) -> None:
                        if action_kind == "key":
                            pyautogui.press(str(action_value))
                        elif action_kind == "hotkey" and isinstance(action_value, (tuple, list)):
                            pyautogui.hotkey(*(str(part) for part in action_value))
                        else:
                            raise ValueError(f"Unsupported voice action: {action_kind}")

                    if kind in {"key", "hotkey"}:
                        run_action(kind, value)
                        ok = True
                    elif kind == "sequence" and isinstance(value, (tuple, list)):
                        for step in value:
                            if not isinstance(step, (tuple, list)) or len(step) != 2:
                                raise ValueError(f"Bad voice action step: {step!r}")
                            run_action(str(step[0]), step[1])
                            time.sleep(0.025)
                        ok = True
                    log_info(
                        "Voice control key action sent to current target",
                        command=phrase,
                        kind=kind,
                        current_foreground_hwnd=current_target.foreground_hwnd,
                        current_focus_hwnd=current_target.focus_hwnd,
                    )
                except Exception as exc:
                    log_exception("Voice control key action failed", exc, command=phrase, kind=kind, value=value)
            elif pyautogui is None:
                log_warning("Voice control key action skipped because pyautogui is not installed", command=phrase, kind=kind)

        log_info(
            "Voice control command handled",
            command=phrase,
            action=label,
            kind=kind,
            ok=ok,
            session_id=session_id,
        )
        log_dictation_text(
            "voice_command",
            session_id=session_id,
            origin=origin,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            command=phrase,
            action=label,
            ok=ok,
        )
        if ok:
            self.status_var.set(f"Команда: {label}")
            self.notify(f"🎛 Команда: {label}", kind="success", duration_ms=900)
            if command.get("reset_message_context"):
                self._reset_stream_message_state(session_id, label)
        else:
            self.notify(f"⚠ Команда распознана, но не выполнена: {label}", kind="warning", duration_ms=1800)
        return ok

    def _handle_worker_message(self, msg_type: str, payload: object) -> None:
        if msg_type == "global_hotkey_pressed":
            try:
                hotkey, target, generation = payload  # type: ignore[misc]
            except Exception:
                hotkey, target, generation = self.hotkey_var.get(), get_paste_target(), None
            self.log_hotkey_trace(
                "global_hotkey_dequeued_on_main_thread",
                hotkey=hotkey,
                generation=generation,
                target=self._hotkey_target_snapshot(target),
            )
            self._handle_global_hotkey(str(hotkey), target if isinstance(target, PasteTarget) else None)
            return

        if msg_type == "external_toggle_recording":
            try:
                source, target = payload  # type: ignore[misc]
            except Exception:
                source, target = "unknown", get_paste_target()
            self.log_state(
                "recording",
                "external_toggle_dequeued",
                source=source,
                target=self._hotkey_target_snapshot(target if isinstance(target, PasteTarget) else None),
            )
            self.log_hotkey_trace(
                "external_toggle_dequeued",
                source=source,
                target=self._hotkey_target_snapshot(target if isinstance(target, PasteTarget) else None),
            )
            self._repair_idle_recording_state(f"external_toggle:{source}")
            self.toggle_recording("hotkey", target if isinstance(target, PasteTarget) else None)
            return

        if msg_type == "external_show_window":
            log_info("External show-window request", source=payload)
            self.show_main_window()
            return

        if msg_type == "external_hide_window":
            log_info("External hide-window request", source=payload)
            self.hide_main_window(show_notification=True)
            return

        if msg_type == "external_exit":
            log_info("External exit request", source=payload)
            self.exit_application()
            return

        if msg_type == "stream_result":
            commit_meta: dict[str, object] = {}
            try:
                if isinstance(payload, (tuple, list)) and len(payload) >= 7:
                    session_id, raw, cleaned, origin, stream_mode, is_final, commit_meta = payload[:7]  # type: ignore[misc]
                else:
                    session_id, raw, cleaned, origin, stream_mode, is_final = payload  # type: ignore[misc]
            except Exception:
                session_id, raw, cleaned, origin, stream_mode, is_final = payload  # type: ignore[misc]
                commit_meta = {}
            if session_id != self.recording_session_id:
                return
            if not self.recorder.is_recording and not self.finalizing_recording:
                log_info(
                    "Late realtime stream result ignored after immediate stop",
                    session_id=session_id,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                    commit_meta=commit_meta if isinstance(commit_meta, dict) else {},
                )
                self.log_state(
                    "streaming",
                    "late_stream_result_ignored_after_stop",
                    session_id=session_id,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                )
                if origin == "hotkey":
                    self.log_hotkey_trace(
                        "late_stream_result_ignored_after_stop",
                        session_id=session_id,
                        stream_mode=stream_mode,
                        is_final=is_final,
                    )
                return
            original_raw = raw
            original_cleaned = cleaned
            raw, cleaned, command = self._split_stream_voice_command(raw, cleaned)
            if raw or cleaned:
                self._handle_stream_text_piece(
                    session_id=session_id,
                    raw=raw,
                    cleaned=cleaned,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                )
            if command and stream_mode == "Вставлять фрагментами" and origin == "hotkey":
                self._execute_voice_control_command(
                    command,
                    session_id=session_id,
                    raw_text=original_raw,
                    cleaned_text=original_cleaned,
                    origin=origin,
                )
                return

        elif msg_type == "result":
            raw, cleaned, origin = payload  # type: ignore[misc]
            self.raw_text.delete("1.0", tk.END)
            self.raw_text.insert(tk.END, raw)
            self.clean_text.delete("1.0", tk.END)
            self.clean_text.insert(tk.END, cleaned)
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            self.record_btn.config(text="● Начать запись")
            self.last_result_ready = True

            streaming_insert_used = (
                self.realtime_streaming_mode_var.get() == "Вставлять фрагментами"
                and self.stream_inserted_any
            )
            if origin == "hotkey" and self.auto_paste_hotkey_var.get() and not streaming_insert_used:
                ok = self.paste_result_to_saved_target(show_messages=False)
                inserted_type = "отредактированный" if self.insert_edited_text_var.get() else "распознанный без редактирования"
                if ok:
                    self.status_var.set("Вставлено")
                    self.notify(f"✅ Текст вставлен\nТип: {inserted_type}", kind="success", duration_ms=2600)
                else:
                    self.status_var.set("Ошибка вставки")
                    self.notify("⚠ Текст распознан, но не вставился\nОн скопирован в буфер обмена — нажми Ctrl+V", kind="warning", duration_ms=4500)
            elif streaming_insert_used:
                final_text = cleaned if self.insert_edited_text_var.get() else raw
                tail = self._get_missing_final_tail(self.stream_inserted_text, final_text)
                if tail:
                    ok = self.paste_text_to_current_target(" " + tail, show_messages=False)
                    if ok:
                        self.stream_inserted_text = (self.stream_inserted_text + " " + tail).strip()
                        self.notify(
                            "✅ Стриминг завершён\nДобавлен финальный хвост текста",
                            kind="success",
                            duration_ms=3200,
                        )
                    else:
                        self.notify(
                            "✅ Стриминг завершён\nФинальная версия готова в окне; при необходимости скопируй её вручную",
                            kind="warning",
                            duration_ms=4200,
                        )
                else:
                    self.notify(
                        "✅ Стриминг завершён\nФразы уже вставлены, финальная версия готова в окне",
                        kind="success",
                        duration_ms=3200,
                    )
            else:
                self.notify("✅ Текст распознан и готов", kind="success", duration_ms=2400)

        elif msg_type == "stream_warning":
            session_id, _exc = payload  # type: ignore[misc]
            if session_id != self.recording_session_id:
                return
            # Non-fatal streaming failure. Most often this means CUDA was not ready;
            # app should stay open and either fallback to CPU or keep recording.
            self.status_var.set("Стриминг: предупреждение")
            self.notify(
                "⚠ Стриминг дал ошибку, программа не закрыта\nЕсли выбрана CUDA — попробуй auto или cpu, либо установи CUDA/cuDNN",
                kind="warning",
                duration_ms=4200,
            )

        elif msg_type in {"stream_finished", "stream_finish_timeout"}:
            session_id = int(payload)
            self.log_state("worker_queue", msg_type, session_id=session_id)
            self.log_hotkey_trace("worker_stream_finish_message", message_type=msg_type, payload_session_id=session_id)
            if session_id != self.recording_session_id:
                self.log_state(
                    "worker_queue",
                    "stale_stream_finish_ignored",
                    message_type=msg_type,
                    payload_session_id=session_id,
                    current_session_id=self.recording_session_id,
                )
                return
            timed_out = msg_type == "stream_finish_timeout"
            self.recorder.discard_frames()
            self.streaming_thread = None
            self.finalizing_recording = False
            self.last_wav_path = None
            self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            log_info("Recording finalization finished", session_id=session_id, timed_out=timed_out)
            self.log_state("recording", "finalization_finished", session_id=session_id, timed_out=timed_out)
            self.log_hotkey_trace("finalization_finished", session_id=session_id, timed_out=timed_out)
            if timed_out:
                self.notify(
                    "⚠ Финальный фрагмент слишком долго обрабатывался\nГорячая клавиша снова доступна",
                    kind="warning",
                    duration_ms=2600,
                )
            self._start_pending_hotkey_recording()

        elif msg_type == "error":
            self.status_var.set("Ошибка")
            self.finalizing_recording = False
            self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            self.notify("⚠ Ошибка распознавания текста", kind="error", duration_ms=4000)
            self._show_error("Ошибка обработки", payload)  # type: ignore[arg-type]

    def _start_pending_hotkey_recording(self) -> None:
        if not self.pending_hotkey_start_requested:
            self.log_state("recording", "no_pending_hotkey_start")
            self.log_hotkey_trace("no_pending_hotkey_start_after_finalization")
            return
        target = self.pending_hotkey_start_target
        self.pending_hotkey_start_requested = False
        self.pending_hotkey_start_target = None
        if self.recorder.is_recording or self.finalizing_recording:
            self.log_state("recording", "pending_hotkey_start_skipped_busy")
            return
        log_info("Starting queued hotkey recording", previous_session_id=self.recording_session_id)
        self.log_state("recording", "pending_hotkey_start_launching", previous_session_id=self.recording_session_id)
        self.log_hotkey_trace(
            "pending_hotkey_start_launching",
            previous_session_id=self.recording_session_id,
            target=self._hotkey_target_snapshot(target),
        )
        self.hotkey_ignore_until = max(
            self.hotkey_ignore_until,
            time.monotonic() + HOTKEY_START_GUARD_SECONDS,
        )
        self.root.after(50, lambda target=target: self.start_recording("hotkey", target))

    def _drain_worker_queue(self) -> None:
        try:
            while True:
                msg_type, payload = self.worker_queue.get_nowait()
                if msg_type in {
                    "stream_finished",
                    "stream_finish_timeout",
                    "error",
                    "global_hotkey_pressed",
                    "external_toggle_recording",
                    "external_show_window",
                    "external_hide_window",
                    "external_exit",
                }:
                    self.log_hotkey_trace("worker_queue_drained_message", message_type=msg_type, payload=str(payload)[:500])
                self._handle_worker_message(msg_type, payload)
        except queue.Empty:
            pass

    def _poll_recording_state_watchdog(self) -> None:
        try:
            self._repair_idle_recording_state("watchdog")
        except Exception as exc:
            log_exception("Recording state watchdog failed", exc)
        try:
            self.root.after(1000, self._poll_recording_state_watchdog)
        except Exception:
            pass

    def _poll_worker_queue(self) -> None:
        self._drain_worker_queue()
        self.root.after(100, self._poll_worker_queue)

    def get_raw_text(self) -> str:
        return self.raw_text.get("1.0", tk.END).strip()

    def get_clean_text(self) -> str:
        return self.clean_text.get("1.0", tk.END).strip()

    def get_text_for_insertion(self) -> str:
        if self.insert_edited_text_var.get():
            return self.get_clean_text()
        return self.get_raw_text()

    def copy_result(self, show_messages: bool = True, edited: bool = True) -> bool:
        text = self.get_clean_text() if edited else self.get_raw_text()
        if not text:
            log_warning("Copy requested with empty text", edited=edited)
            if show_messages:
                messagebox.showinfo(APP_NAME, "Нет текста для копирования.")
            return False
        if pyperclip is None:
            log_warning("Copy failed because pyperclip is not installed", edited=edited)
            if show_messages:
                messagebox.showerror(APP_NAME, "Не установлен pyperclip. Выполни: pip install pyperclip")
            return False
        pyperclip.copy(text)
        self.status_var.set("Скопировано")
        log_info("Text copied to clipboard", edited=edited, chars=len(text))
        return True

    def paste_text_to_current_target(self, text: str, show_messages: bool = True) -> bool:
        """Copy provided text and paste it into the currently focused field/window.

        Important realtime behavior: do not restore the window that was active
        when dictation started. The user can move the caret to another app or
        another input field while recording, and the next chunk will be inserted
        exactly there.
        """
        text = str(text or "")
        # Whitespace-only text is valid for voice commands: "новая строка",
        # "новый абзац", "пробел" and "табуляция". Only a truly empty
        # string should be rejected.
        if text == "":
            log_warning("Paste requested with empty text")
            if show_messages:
                messagebox.showinfo(APP_NAME, "Нет текста для вставки.")
            return False
        if pyperclip is None:
            log_warning("Paste failed because pyperclip is not installed")
            if show_messages:
                messagebox.showerror(
                    APP_NAME,
                    "Для вставки нужен pyperclip. Выполни: pip install pyperclip",
                )
            return False

        try:
            # Copy first, then send Ctrl+V to the currently active field.
            # No SetForegroundWindow/restore call is used here on purpose.
            current_target = get_paste_target()
            pyperclip.copy(text)
            time.sleep(0.025)
            if not send_ctrl_v_native():
                raise RuntimeError("Не удалось отправить Ctrl+V")
            self.status_var.set("Вставлено")
            log_info(
                "Text pasted to current target",
                chars=len(text),
                current_foreground_hwnd=current_target.foreground_hwnd,
                current_focus_hwnd=current_target.focus_hwnd,
            )
            log_category(
                "insertion",
                "paste_success",
                chars=len(text),
                current_foreground_hwnd=current_target.foreground_hwnd,
                current_focus_hwnd=current_target.focus_hwnd,
            )
            return True
        except Exception as exc:
            log_exception("Could not paste text to current target", exc, chars=len(text))
            log_category("insertion", "paste_failed", chars=len(text), error=str(exc))
            # Keep text in clipboard for manual Ctrl+V.
            if show_messages:
                self._show_error("Не удалось вставить текст", exc)
            else:
                print("Не удалось вставить текст", exc, file=sys.stderr)
            return False

    def paste_text_to_saved_target(self, text: str, show_messages: bool = True) -> bool:
        """Compatibility wrapper: realtime insertion now follows the current cursor."""
        return self.paste_text_to_current_target(text, show_messages=show_messages)

    def paste_result_to_saved_target(self, show_messages: bool = True) -> bool:
        """Copy chosen text and paste it into the currently focused field/window."""
        return self.paste_text_to_current_target(self.get_text_for_insertion(), show_messages=show_messages)

    def paste_result(self) -> None:
        ok = self.copy_result(show_messages=True, edited=self.insert_edited_text_var.get())
        if ok:
            self.notify("✅ Текст скопирован\nПерейди в нужное поле и нажми Ctrl+V", kind="success", duration_ms=3200)

    def clear_texts(self, keep_status: bool = False) -> None:
        self.raw_text.delete("1.0", tk.END)
        self.clean_text.delete("1.0", tk.END)
        self.timer_var.set("00:00")
        self.last_result_ready = False
        if not keep_status:
            self.status_var.set("Готово")

    def hide_main_window(self, show_notification: bool = True) -> None:
        try:
            self.hotkey_entry_capture_active = False
            self.hotkey_entry_pressed.clear()
            self.hotkey_entry_pressed_order = []
            if self.root.winfo_viewable():
                self._save_settings()
            if self.tray_started:
                self.root.withdraw()
                log_info("Main window hidden to tray/background")
                if not show_notification:
                    print(
                        f"{APP_NAME} запущен в трее. Горячая клавиша: {pretty_hotkey(self.hotkey_var.get())}. "
                        "Чтобы открыть окно, найди значок VoiceFlow рядом с часами или запусти с --show.",
                        file=sys.stderr,
                    )
            else:
                self.root.iconify()
                log_info("Main window minimized because tray icon is unavailable")
                if not show_notification:
                    print(
                        f"{APP_NAME} запущен, но значок трея недоступен. "
                        "Установи: py -m pip install pystray pillow",
                        file=sys.stderr,
                    )
        except Exception as exc:
            log_exception("Could not hide main window", exc)
        if show_notification and self.show_notifications_var.get():
            suffix = "\nЗначок трея недоступен: установи pystray pillow" if not self.tray_started else ""
            self.notify(
                f"VoiceFlow работает в фоне\nГорячая клавиша: {pretty_hotkey(self.hotkey_var.get())}{suffix}",
                kind="idle",
                duration_ms=2600,
            )

    def show_main_window(self) -> None:
        self.root.deiconify()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        self.root.focus_force()
        log_info("Main window shown")

    def _save_settings(self) -> None:
        self.settings.mode = self.mode_var.get()
        self.settings.language = self.language_var.get()
        self.settings.privacy_mode = self.privacy_var.get()
        self.settings.microphone_label = self.microphone_var.get()
        self.settings.hotkey = normalize_hotkey(self.hotkey_var.get())
        self.settings.auto_paste_after_hotkey = self.auto_paste_hotkey_var.get()
        self.settings.insert_edited_text = self.insert_edited_text_var.get()
        self.settings.show_notifications = self.show_notifications_var.get()
        self.settings.launch_at_startup = self.launch_at_startup_var.get()
        self.settings.whisper_model = self.whisper_model_var.get() if self.whisper_model_var.get() in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL
        self.settings.recognition_quality = self.recognition_quality_var.get() if self.recognition_quality_var.get() in QUALITY_OPTIONS else "Максимальная точность"
        self.settings.inference_device = self.inference_device_var.get() if self.inference_device_var.get() in INFERENCE_DEVICE_OPTIONS else "auto"
        self.settings.compute_type = self.compute_type_var.get() if self.compute_type_var.get() in COMPUTE_TYPE_OPTIONS else "auto"
        self.settings.use_vad_filter = self.use_vad_filter_var.get()
        self.settings.custom_terms = self.custom_terms_var.get()
        self.settings.deep_grammar = self.deep_grammar_var.get()
        self.settings.realtime_streaming_mode = (
            self.realtime_streaming_mode_var.get()
            if self.realtime_streaming_mode_var.get() in STREAMING_MODE_OPTIONS
            else "Вставлять фрагментами"
        )
        try:
            self.settings.realtime_chunk_seconds = int(self.realtime_chunk_seconds_var.get())
        except Exception:
            self.settings.realtime_chunk_seconds = 4
        self.settings.realtime_fast_quality = self.realtime_fast_quality_var.get()
        self.settings.realtime_speed_profile = self.realtime_speed_profile_var.get() if self.realtime_speed_profile_var.get() in STREAMING_SPEED_OPTIONS else "Быстрее"
        try:
            notification_position = self.notifications.manual_position
            if notification_position is None:
                self.settings.notification_x = None
                self.settings.notification_y = None
            else:
                self.settings.notification_x = int(notification_position[0])
                self.settings.notification_y = int(notification_position[1])
        except Exception:
            pass
        try:
            if self.root.winfo_exists():
                self.settings.window_geometry = self.root.geometry()
        except Exception:
            pass
        SettingsStore.save(self.settings)

    def apply_startup_setting(self) -> None:
        enabled = self.launch_at_startup_var.get()
        if not IS_WINDOWS:
            self.launch_at_startup_var.set(False)
            log_warning("Startup setting requested on non-Windows platform")
            messagebox.showwarning(APP_NAME, "Автозапуск доступен только на Windows.")
            return
        try:
            set_windows_startup_enabled(enabled)
            self.settings.launch_at_startup = enabled
            self._save_settings()
            log_info("Startup setting changed", enabled=enabled)
            if enabled:
                self.notify("✅ Автозапуск включён\nVoiceFlow будет запускаться вместе с Windows 11", kind="success", duration_ms=3200)
            else:
                self.notify("Автозапуск выключен", kind="idle", duration_ms=2200)
        except Exception as exc:
            log_exception("Could not change startup setting", exc, enabled=enabled)
            self.launch_at_startup_var.set(is_windows_startup_enabled())
            self._show_error("Не удалось изменить автозапуск", exc)

    def _show_error(self, title: str, exc: object) -> None:
        details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if isinstance(exc, BaseException):
            log_exception("UI error shown", exc, title=title)
        else:
            log_event(logging.ERROR, "UI error shown", title=title, details=details)
        messagebox.showerror(APP_NAME, f"{title}:\n\n{details}")
        print(title, details, file=sys.stderr)

    def on_close(self) -> None:
        if not self.exit_requested:
            log_info("Window close requested; hiding instead of exiting")
            self.hide_main_window(show_notification=True)
            return
        log_info("Application close requested")
        try:
            self.log_hotkey_trace("application_close_requested")
        except Exception:
            pass
        self._save_settings()
        try:
            if self.transcriber_warmup_job is not None:
                self.root.after_cancel(self.transcriber_warmup_job)
                self.transcriber_warmup_job = None
        except Exception:
            pass
        try:
            self._stop_windows_hotkey_polling()
        except Exception:
            pass
        try:
            if keyboard is not None and self.hotkey_handle is not None:
                keyboard.remove_hotkey(self.hotkey_handle)
        except Exception:
            pass
        try:
            self._stop_realtime_streaming()
        except Exception:
            pass
        try:
            self.notifications.destroy()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def exit_application(self) -> None:
        self.exit_requested = True
        self.on_close()


def main() -> None:
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


if __name__ == "__main__":
    main()
