from __future__ import annotations

import queue
import unittest

from voiceflow_app.worker_messages import (
    HotkeyPressedPayload,
    StreamResultDisposition,
    StreamResultPayload,
    WorkerMessage,
    WorkerMessageKind,
    classify_stream_result,
    coerce_worker_message,
    is_current_session,
    put_worker_message,
)


class WorkerMessageContractTests(unittest.TestCase):
    def test_typed_message_round_trip(self) -> None:
        q: queue.Queue[object] = queue.Queue()
        payload = StreamResultPayload(
            session_id=7,
            raw="raw",
            cleaned="clean",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            is_final=False,
            commit_meta={"pause_seconds": 0.4},
        )
        put_worker_message(q, WorkerMessageKind.STREAM_RESULT, payload)
        message = coerce_worker_message(q.get_nowait())
        self.assertEqual(message.kind, WorkerMessageKind.STREAM_RESULT)
        self.assertEqual(message.payload, payload)

    def test_legacy_tuple_is_still_accepted_during_migration(self) -> None:
        message = coerce_worker_message(("external_exit", "legacy"))
        self.assertEqual(message, WorkerMessage(WorkerMessageKind.EXTERNAL_EXIT, "legacy"))

    def test_hotkey_payload_is_explicit(self) -> None:
        payload = HotkeyPressedPayload("ctrl+shift+space", object(), generation=3)
        self.assertEqual(payload.hotkey, "ctrl+shift+space")
        self.assertEqual(payload.generation, 3)

    def test_stream_result_stale_and_after_stop_gates(self) -> None:
        self.assertEqual(
            classify_stream_result(
                message_session_id=1,
                current_session_id=2,
                recorder_active=True,
                finalizing=False,
            ),
            StreamResultDisposition.STALE_SESSION,
        )
        self.assertEqual(
            classify_stream_result(
                message_session_id=2,
                current_session_id=2,
                recorder_active=False,
                finalizing=False,
            ),
            StreamResultDisposition.AFTER_STOP,
        )
        self.assertEqual(
            classify_stream_result(
                message_session_id=2,
                current_session_id=2,
                recorder_active=False,
                finalizing=True,
            ),
            StreamResultDisposition.ACCEPT,
        )

    def test_finish_gate_uses_session_identity(self) -> None:
        self.assertTrue(is_current_session(9, 9))
        self.assertFalse(is_current_session(8, 9))


if __name__ == "__main__":
    unittest.main()
