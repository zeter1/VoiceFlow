"""Third-party runtime dependency loading and audio-device discovery."""

from __future__ import annotations

import sys

from .diagnostics import log_exception

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

