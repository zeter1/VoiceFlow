from __future__ import annotations

import unittest

from voiceflow_app.core.hotkey_state import (
    HotkeyEdgeState,
    advance_hotkey_edge,
    decide_hotkey_action,
)


class HotkeyStateTests(unittest.TestCase):
    def test_press_hold_release_rearms_only_after_stable_release(self) -> None:
        state = HotkeyEdgeState()
        pressed = advance_hotkey_edge(
            state,
            all_down=True,
            now=10.0,
            min_edge_gap_seconds=0.55,
            release_stable_seconds=0.10,
        )
        self.assertEqual(pressed.event, "press")

        held = advance_hotkey_edge(
            pressed.state,
            all_down=True,
            now=10.4,
            min_edge_gap_seconds=0.55,
            release_stable_seconds=0.10,
        )
        self.assertEqual(held.event, "held")

        releasing = advance_hotkey_edge(
            held.state,
            all_down=False,
            now=10.5,
            min_edge_gap_seconds=0.55,
            release_stable_seconds=0.10,
        )
        self.assertEqual(releasing.event, "release_started")

        waiting = advance_hotkey_edge(
            releasing.state,
            all_down=False,
            now=10.55,
            min_edge_gap_seconds=0.55,
            release_stable_seconds=0.10,
        )
        self.assertEqual(waiting.event, "release_wait")
        self.assertTrue(waiting.state.is_down)

        rearmed = advance_hotkey_edge(
            waiting.state,
            all_down=False,
            now=10.61,
            min_edge_gap_seconds=0.55,
            release_stable_seconds=0.10,
        )
        self.assertEqual(rearmed.event, "rearmed")
        self.assertFalse(rearmed.state.is_down)

    def test_micro_flicker_press_is_ignored(self) -> None:
        state = HotkeyEdgeState(last_press_at=10.0)
        transition = advance_hotkey_edge(
            state,
            all_down=True,
            now=10.2,
            min_edge_gap_seconds=0.55,
            release_stable_seconds=0.10,
        )
        self.assertEqual(transition.event, "ignored_press_too_close")
        self.assertTrue(transition.state.is_down)
        self.assertEqual(transition.state.last_press_at, 10.0)

    def test_hotkey_action_decision_covers_debounce_start_stop_and_finalizing(self) -> None:
        self.assertEqual(
            decide_hotkey_action(
                now=1.0,
                ignore_until=1.2,
                start_in_progress=False,
                is_recording=False,
                finalizing=False,
            ).action,
            "ignore_debounce",
        )
        self.assertEqual(
            decide_hotkey_action(
                now=2.0,
                ignore_until=0.0,
                start_in_progress=True,
                is_recording=False,
                finalizing=False,
            ).action,
            "ignore_start_in_progress",
        )
        self.assertEqual(
            decide_hotkey_action(
                now=2.0,
                ignore_until=0.0,
                start_in_progress=False,
                is_recording=True,
                finalizing=False,
            ).action,
            "stop_recording",
        )
        self.assertEqual(
            decide_hotkey_action(
                now=2.0,
                ignore_until=0.0,
                start_in_progress=False,
                is_recording=False,
                finalizing=True,
            ).action,
            "queue_start_after_finalizing",
        )
        self.assertEqual(
            decide_hotkey_action(
                now=2.0,
                ignore_until=0.0,
                start_in_progress=False,
                is_recording=False,
                finalizing=False,
            ).action,
            "start_recording",
        )


if __name__ == "__main__":
    unittest.main()
