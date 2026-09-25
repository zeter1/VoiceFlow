from __future__ import annotations

import unittest

from voiceflow_app.core.realtime_policy import (
    build_realtime_timing,
    decide_chunk_commit,
    should_process_final_chunk,
)


class RealtimePolicyTests(unittest.TestCase):
    def test_fast_profile_timing_matches_existing_contract(self) -> None:
        timing = build_realtime_timing("Быстрее", 4, use_vad_filter=False, cpu_path_expected=True)
        self.assertEqual(timing.poll_interval, 0.18)
        self.assertEqual(timing.min_seconds, 1.35)
        self.assertEqual(timing.max_seconds, 2.8)
        self.assertEqual(timing.trailing_silence_required, 0.24)
        self.assertFalse(timing.streaming_vad_filter)

    def test_quality_cpu_profile_is_more_conservative(self) -> None:
        timing = build_realtime_timing("Качество", 4, use_vad_filter=False, cpu_path_expected=True)
        self.assertEqual(timing.poll_interval, 0.32)
        self.assertEqual(timing.min_seconds, 2.4)
        self.assertEqual(timing.max_seconds, 5.0)
        self.assertEqual(timing.trailing_silence_required, 0.55)
        self.assertTrue(timing.streaming_vad_filter)

    def test_short_chunk_waits_without_advancing(self) -> None:
        timing = build_realtime_timing("Быстрее", 4, use_vad_filter=False, cpu_path_expected=True)
        decision = decide_chunk_commit(
            {"duration": 0.8, "peak": 0.2, "rms": 0.05, "pause_seconds": 0.5},
            timing,
        )
        self.assertFalse(decision.commit)
        self.assertFalse(decision.advance_frame)
        self.assertEqual(decision.reason, "below_min_duration")

    def test_non_speech_chunk_is_skipped_and_advanced(self) -> None:
        timing = build_realtime_timing("Быстрее", 4, use_vad_filter=False, cpu_path_expected=True)
        decision = decide_chunk_commit(
            {"duration": 1.5, "peak": 0.005, "rms": 0.001, "pause_seconds": 0.5},
            timing,
        )
        self.assertFalse(decision.commit)
        self.assertTrue(decision.advance_frame)
        self.assertEqual(decision.reason, "not_probable_speech")

    def test_pause_or_max_duration_commits(self) -> None:
        timing = build_realtime_timing("Быстрее", 4, use_vad_filter=False, cpu_path_expected=True)
        pause = decide_chunk_commit(
            {"duration": 1.6, "peak": 0.2, "rms": 0.05, "pause_seconds": 0.3},
            timing,
        )
        forced = decide_chunk_commit(
            {"duration": 3.0, "peak": 0.2, "rms": 0.05, "pause_seconds": 0.0},
            timing,
        )
        self.assertTrue(pause.commit)
        self.assertTrue(pause.has_pause)
        self.assertTrue(forced.commit)
        self.assertTrue(forced.forced_commit)

    def test_final_quiet_chunk_is_not_committed(self) -> None:
        timing = build_realtime_timing("Баланс", 4, use_vad_filter=True, cpu_path_expected=True)
        decision = decide_chunk_commit(
            {"duration": 0.5, "peak": 0.004, "rms": 0.001, "pause_seconds": 0.4},
            timing,
            is_final=True,
        )
        self.assertFalse(decision.commit)
        self.assertTrue(decision.advance_frame)

    def test_stop_cancellation_policy_preserves_responsive_hotkey_contract(self) -> None:
        self.assertFalse(should_process_final_chunk(stop_requested=True, final_chunk_on_stop=False))
        self.assertTrue(should_process_final_chunk(stop_requested=True, final_chunk_on_stop=True))


if __name__ == "__main__":
    unittest.main()
