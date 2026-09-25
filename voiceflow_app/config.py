"""Application paths and stable runtime configuration constants."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


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
LOG_DIR = APP_DIR / "Логи проблем"
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
IS_WINDOWS = sys.platform.startswith("win")
CUDA_REQUIRED_WINDOWS_DLLS = ("cublas64_12.dll", "cudnn_ops64_9.dll")

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

