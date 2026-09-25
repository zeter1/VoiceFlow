"""Third-party runtime dependency loading and audio-device discovery."""

from __future__ import annotations

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
