from __future__ import annotations

import unittest

from voiceflow_app.services.audio_analysis import (
    activity_threshold_for_rms,
    adaptive_speech_threshold,
    build_audio_stats,
)


class RealtimeAudioAnalysisTests(unittest.TestCase):
    def test_activity_threshold_is_bounded(self) -> None:
        self.assertEqual(activity_threshold_for_rms(0.0), 0.006)
        self.assertAlmostEqual(activity_threshold_for_rms(0.02), 0.0144)
        self.assertEqual(activity_threshold_for_rms(1.0), 0.028)

    def test_adaptive_threshold_tracks_noise_but_stays_bounded(self) -> None:
        self.assertEqual(adaptive_speech_threshold(0.0, 0.0, 0.0), 0.006)
        self.assertAlmostEqual(adaptive_speech_threshold(0.01, 0.012, 0.04), 0.021)
        self.assertEqual(adaptive_speech_threshold(1.0, 1.0, 1.0), 0.075)

    def test_audio_stats_preserve_noisy_room_pause_signal(self) -> None:
        stats = build_audio_stats(
            sample_count=16000,
            sample_rate=16000,
            peak=0.20,
            rms=0.05,
            active_count=8000,
            last_active_sample=15000,
            activity_threshold=0.028,
            window_size=960,
            window_count=16,
            speech_threshold=0.04,
            noise_floor_rms=0.01,
            speech_high_rms=0.08,
            last_speech_window=10,
        )
        self.assertAlmostEqual(stats["duration"], 1.0)
        self.assertAlmostEqual(stats["active_ratio"], 0.5)
        self.assertAlmostEqual(stats["trailing_silence"], 999 / 16000)
        self.assertAlmostEqual(stats["speech_trailing_silence"], 5 * 960 / 16000)
        self.assertAlmostEqual(stats["pause_seconds"], 0.3)
        self.assertAlmostEqual(stats["speech_threshold"], 0.04)

    def test_empty_stats_are_safe_and_complete(self) -> None:
        stats = build_audio_stats(
            sample_count=0,
            sample_rate=16000,
            peak=0.0,
            rms=0.0,
            active_count=0,
            last_active_sample=None,
            activity_threshold=0.006,
        )
        for key in (
            "duration", "rms", "peak", "active_ratio",
            "trailing_silence", "speech_trailing_silence", "pause_seconds",
        ):
            self.assertEqual(stats[key], 0.0)


if __name__ == "__main__":
    unittest.main()
