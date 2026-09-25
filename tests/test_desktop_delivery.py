from __future__ import annotations

import unittest

from voiceflow_app.app.ports import DeliveryResult
from voiceflow_app.desktop_delivery import (
    CurrentTargetTextInsertionAdapter,
    CurrentTargetVoiceActionAdapter,
)
from voiceflow_app.windows_insertion import PasteTarget


class FakeClipboard:
    def __init__(self):
        self.copied = []

    def copy(self, text):
        self.copied.append(text)


class FakeAutomation:
    def __init__(self):
        self.calls = []

    def press(self, key):
        self.calls.append(("press", key))

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))


class FakeTextInserter:
    def __init__(self):
        self.texts = []

    def paste_current(self, text):
        self.texts.append(text)
        return DeliveryResult(ok=True, code="pasted")


class DesktopDeliveryAdapterTests(unittest.TestCase):
    def test_text_adapter_preserves_whitespace_only_voice_payloads(self):
        clipboard = FakeClipboard()
        sleeps = []
        adapter = CurrentTargetTextInsertionAdapter(
            clipboard=clipboard,
            target_getter=lambda: PasteTarget(10, 20),
            paste_sender=lambda: True,
            sleep=sleeps.append,
        )

        result = adapter.paste_current("\n")

        self.assertTrue(result.ok)
        self.assertEqual(clipboard.copied, ["\n"])
        self.assertEqual(result.foreground_hwnd, 10)
        self.assertEqual(result.focus_hwnd, 20)
        self.assertEqual(sleeps, [0.025])

    def test_text_adapter_leaves_clipboard_payload_when_paste_sender_fails(self):
        clipboard = FakeClipboard()
        adapter = CurrentTargetTextInsertionAdapter(
            clipboard=clipboard,
            target_getter=lambda: PasteTarget(1, 2),
            paste_sender=lambda: False,
            sleep=lambda _seconds: None,
        )

        result = adapter.paste_current("fallback")

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "paste_failed")
        self.assertEqual(clipboard.copied, ["fallback"])

    def test_voice_text_command_delegates_to_text_insertion_port(self):
        inserter = FakeTextInserter()
        adapter = CurrentTargetVoiceActionAdapter(
            inserter,
            automation=FakeAutomation(),
            target_getter=lambda: PasteTarget(1, 2),
            sleep=lambda _seconds: None,
        )

        result = adapter.execute({"kind": "text", "value": "\n"})

        self.assertTrue(result.ok)
        self.assertEqual(inserter.texts, ["\n"])

    def test_voice_key_hotkey_and_sequence_use_injected_automation(self):
        automation = FakeAutomation()
        adapter = CurrentTargetVoiceActionAdapter(
            FakeTextInserter(),
            automation=automation,
            target_getter=lambda: PasteTarget(7, 8),
            sleep=lambda _seconds: None,
        )

        self.assertTrue(adapter.execute({"kind": "key", "value": "enter"}).ok)
        self.assertTrue(
            adapter.execute({"kind": "hotkey", "value": ("ctrl", "z")}).ok
        )
        self.assertTrue(
            adapter.execute(
                {
                    "kind": "sequence",
                    "value": (
                        ("key", "home"),
                        ("hotkey", ("shift", "end")),
                        ("key", "backspace"),
                    ),
                }
            ).ok
        )

        self.assertEqual(
            automation.calls,
            [
                ("press", "enter"),
                ("hotkey", ("ctrl", "z")),
                ("press", "home"),
                ("hotkey", ("shift", "end")),
                ("press", "backspace"),
            ],
        )

    def test_missing_automation_is_structured_failure(self):
        adapter = CurrentTargetVoiceActionAdapter(
            FakeTextInserter(),
            automation=None,
            target_getter=lambda: PasteTarget(7, 8),
            sleep=lambda _seconds: None,
        )

        result = adapter.execute({"kind": "key", "value": "enter"})

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "pyautogui_missing")
        self.assertEqual(result.foreground_hwnd, 7)


if __name__ == "__main__":
    unittest.main()
