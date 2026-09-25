"""Windows foreground-target and native paste adapter."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import time
from typing import Optional

from .config import IS_WINDOWS
from .dependencies import pyautogui


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
