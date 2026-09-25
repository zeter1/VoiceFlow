from __future__ import annotations

import unittest

from voiceflow_app.core.recording_state import (
    RecordingPhase,
    RecordingStateSnapshot,
    derive_recording_phase,
    needs_idle_repair,
    recording_toggle_intent,
)


class RecordingStateTests(unittest.TestCase):
    def test_clean_idle_state_needs_no_repair(self) -> None:
        snapshot = RecordingStateSnapshot(is_recording=False, status="Готово")
        self.assertEqual(derive_recording_phase(snapshot), RecordingPhase.IDLE)
        self.assertFalse(needs_idle_repair(snapshot))
        self.assertEqual(recording_toggle_intent(snapshot), "start_recording")

    def test_recording_state_stops_instead_of_starting_again(self) -> None:
        snapshot = RecordingStateSnapshot(is_recording=True, status="Идёт запись...")
        self.assertEqual(derive_recording_phase(snapshot), RecordingPhase.RECORDING)
        self.assertEqual(recording_toggle_intent(snapshot), "stop_recording")

    def test_stale_finalizing_or_busy_ui_is_repairable_idle(self) -> None:
        for snapshot in (
            RecordingStateSnapshot(is_recording=False, finalizing=True),
            RecordingStateSnapshot(is_recording=False, status="Обрабатываю последний фрагмент..."),
            RecordingStateSnapshot(is_recording=False, button_state="disabled"),
            RecordingStateSnapshot(is_recording=False, streaming_thread_present=True),
        ):
            with self.subTest(snapshot=snapshot):
                self.assertEqual(derive_recording_phase(snapshot), RecordingPhase.STALE_IDLE)
                self.assertTrue(needs_idle_repair(snapshot))
                self.assertEqual(recording_toggle_intent(snapshot), "repair_then_start")

    def test_start_in_progress_is_a_distinct_guard_state(self) -> None:
        snapshot = RecordingStateSnapshot(is_recording=False, start_in_progress=True)
        self.assertEqual(derive_recording_phase(snapshot), RecordingPhase.STARTING)
        self.assertEqual(recording_toggle_intent(snapshot), "ignore_start_in_progress")


if __name__ == "__main__":
    unittest.main()
