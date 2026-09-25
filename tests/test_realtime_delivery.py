from __future__ import annotations

import unittest

from voiceflow_app.app.ports import DeliveryResult
from voiceflow_app.app.realtime_delivery import RealtimeDeliveryController
from voiceflow_app.app.realtime_text_pipeline import RealtimeInsertionPlan
from voiceflow_app.core.realtime import PasteChunkDecision


class FakeTextInserter:
    def __init__(self, result: DeliveryResult):
        self.result = result
        self.texts = []

    def paste_current(self, text: str) -> DeliveryResult:
        self.texts.append(text)
        return self.result


class FakeVoiceActionExecutor:
    def __init__(self, result: DeliveryResult):
        self.result = result
        self.commands = []

    def execute(self, command: dict[str, object]) -> DeliveryResult:
        self.commands.append(command)
        return self.result


def insertion_plan(text: str = "Привет.") -> RealtimeInsertionPlan:
    return RealtimeInsertionPlan(
        text=text,
        paste_text=text + " ",
        selected_source="cleaned",
        punctuation=PasteChunkDecision(
            text=text,
            pause_seconds=1.2,
            sentence_pause=True,
            whisper_sentence_end=False,
            previous_had_sentence_end=False,
            forced_commit=False,
        ),
    )


class RealtimeDeliveryTests(unittest.TestCase):
    def test_exact_paste_payload_is_sent_and_commit_is_returned_only_on_success(self):
        inserter = FakeTextInserter(
            DeliveryResult(
                ok=True,
                code="pasted",
                foreground_hwnd=11,
                focus_hwnd=22,
            )
        )
        voice = FakeVoiceActionExecutor(DeliveryResult(ok=True))
        controller = RealtimeDeliveryController(inserter, voice)

        outcome = controller.deliver_insertion(insertion_plan())

        self.assertEqual(inserter.texts, ["Привет. "])
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.committed_text, "Привет.")
        self.assertEqual(outcome.paste_text, "Привет. ")
        self.assertEqual(outcome.foreground_hwnd, 11)
        self.assertEqual(outcome.focus_hwnd, 22)

    def test_failed_paste_never_returns_committed_text(self):
        inserter = FakeTextInserter(
            DeliveryResult(
                ok=False,
                code="paste_failed",
                error="blocked target",
            )
        )
        controller = RealtimeDeliveryController(
            inserter,
            FakeVoiceActionExecutor(DeliveryResult(ok=True)),
        )

        outcome = controller.deliver_insertion(insertion_plan("Не вставлено"))

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.committed_text, "")
        self.assertEqual(outcome.code, "paste_failed")
        self.assertEqual(outcome.error, "blocked target")

    def test_voice_command_outcome_preserves_reset_and_target_evidence(self):
        voice = FakeVoiceActionExecutor(
            DeliveryResult(
                ok=True,
                code="voice_action_sent",
                foreground_hwnd=33,
                focus_hwnd=44,
            )
        )
        controller = RealtimeDeliveryController(
            FakeTextInserter(DeliveryResult(ok=True)),
            voice,
        )
        command = {
            "kind": "key",
            "value": "enter",
            "phrase": "отправь сообщение",
            "label": "отправить сообщение",
            "reset_message_context": True,
        }

        outcome = controller.execute_voice_command(command)

        self.assertEqual(voice.commands, [command])
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.kind, "key")
        self.assertEqual(outcome.phrase, "отправь сообщение")
        self.assertEqual(outcome.label, "отправить сообщение")
        self.assertTrue(outcome.reset_message_context)
        self.assertEqual(outcome.foreground_hwnd, 33)


if __name__ == "__main__":
    unittest.main()
