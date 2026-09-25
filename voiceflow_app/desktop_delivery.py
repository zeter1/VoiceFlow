"""Desktop text-insertion and voice-action adapters.

This module is the concrete side-effect boundary. Application/realtime logic
depends on stdlib-only ports from app.ports instead of importing pyautogui,
clipboard or Windows focus helpers directly.
"""

from __future__ import annotations

import time
from typing import Callable

from .app.ports import DeliveryResult, TextInsertionPort
from .dependencies import pyautogui, pyperclip
from .windows_insertion import PasteTarget, get_paste_target, send_ctrl_v_native


class CurrentTargetTextInsertionAdapter:
    def __init__(
        self,
        *,
        clipboard: object = pyperclip,
        target_getter: Callable[[], PasteTarget] = get_paste_target,
        paste_sender: Callable[[], bool] = send_ctrl_v_native,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.clipboard = clipboard
        self.target_getter = target_getter
        self.paste_sender = paste_sender
        self.sleep = sleep

    def paste_current(self, text: str) -> DeliveryResult:
        text = str(text or "")
        if text == "":
            return DeliveryResult(ok=False, code="empty_text", error="No text to paste")
        if self.clipboard is None:
            return DeliveryResult(
                ok=False,
                code="pyperclip_missing",
                error="pyperclip is not installed",
            )

        target = self.target_getter()
        try:
            copy = getattr(self.clipboard, "copy")
            copy(text)
            self.sleep(0.025)
            if not self.paste_sender():
                raise RuntimeError("Не удалось отправить Ctrl+V")
            return DeliveryResult(
                ok=True,
                code="pasted",
                foreground_hwnd=target.foreground_hwnd,
                focus_hwnd=target.focus_hwnd,
            )
        except Exception as exc:
            return DeliveryResult(
                ok=False,
                code="paste_failed",
                error=str(exc) or type(exc).__name__,
                foreground_hwnd=target.foreground_hwnd,
                focus_hwnd=target.focus_hwnd,
            )


class CurrentTargetVoiceActionAdapter:
    def __init__(
        self,
        text_inserter: TextInsertionPort,
        *,
        automation: object = pyautogui,
        target_getter: Callable[[], PasteTarget] = get_paste_target,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.text_inserter = text_inserter
        self.automation = automation
        self.target_getter = target_getter
        self.sleep = sleep

    def execute(self, command: dict[str, object]) -> DeliveryResult:
        kind = str(command.get("kind", ""))
        value = command.get("value")

        if kind == "text":
            return self.text_inserter.paste_current(str(value or ""))

        target = self.target_getter()
        if self.automation is None:
            return DeliveryResult(
                ok=False,
                code="pyautogui_missing",
                error="pyautogui is not installed",
                foreground_hwnd=target.foreground_hwnd,
                focus_hwnd=target.focus_hwnd,
            )

        def run_action(action_kind: str, action_value: object) -> None:
            if action_kind == "key":
                getattr(self.automation, "press")(str(action_value))
                return
            if action_kind == "hotkey" and isinstance(action_value, (tuple, list)):
                getattr(self.automation, "hotkey")(*(str(part) for part in action_value))
                return
            raise ValueError(f"Unsupported voice action: {action_kind}")

        try:
            self.sleep(0.025)
            if kind in {"key", "hotkey"}:
                run_action(kind, value)
            elif kind == "sequence" and isinstance(value, (tuple, list)):
                for step in value:
                    if not isinstance(step, (tuple, list)) or len(step) != 2:
                        raise ValueError(f"Bad voice action step: {step!r}")
                    run_action(str(step[0]), step[1])
                    self.sleep(0.025)
            else:
                raise ValueError(f"Unsupported voice action: {kind}")
            return DeliveryResult(
                ok=True,
                code="voice_action_sent",
                foreground_hwnd=target.foreground_hwnd,
                focus_hwnd=target.focus_hwnd,
            )
        except Exception as exc:
            return DeliveryResult(
                ok=False,
                code="voice_action_failed",
                error=str(exc) or type(exc).__name__,
                foreground_hwnd=target.foreground_hwnd,
                focus_hwnd=target.focus_hwnd,
            )
