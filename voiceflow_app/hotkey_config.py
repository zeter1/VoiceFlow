"""Hotkey normalization and Windows virtual-key mapping."""

from __future__ import annotations

import ctypes
import re
from typing import Optional

from .config import (
    DEFAULT_HOTKEY,
    HOTKEY_MODIFIER_ORDER,
    HOTKEY_MODIFIERS,
    HOTKEY_SPECIAL_KEYS,
    IS_WINDOWS,
    WINDOWS_HOTKEY_VK,
)


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

