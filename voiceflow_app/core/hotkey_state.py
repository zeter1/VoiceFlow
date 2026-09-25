"""Pure global-hotkey edge and action state machines."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class HotkeyEdgeState:
    is_down: bool = False
    press_started_at: Optional[float] = None
    release_started_at: Optional[float] = None
    last_press_at: float = 0.0


@dataclass(frozen=True)
class HotkeyEdgeTransition:
    state: HotkeyEdgeState
    event: str
    edge_gap: Optional[float] = None
    held_for: Optional[float] = None
    release_stable_for: Optional[float] = None


def advance_hotkey_edge(
    state: HotkeyEdgeState,
    *,
    all_down: bool,
    now: float,
    min_edge_gap_seconds: float,
    release_stable_seconds: float,
) -> HotkeyEdgeTransition:
    if all_down:
        state = replace(state, release_started_at=None)
        if not state.is_down:
            edge_gap = now - state.last_press_at if state.last_press_at else None
            if edge_gap is not None and edge_gap < min_edge_gap_seconds:
                new_state = replace(state, is_down=True, press_started_at=now)
                return HotkeyEdgeTransition(new_state, "ignored_press_too_close", edge_gap=edge_gap)
            new_state = replace(
                state,
                is_down=True,
                press_started_at=now,
                last_press_at=now,
            )
            return HotkeyEdgeTransition(new_state, "press", edge_gap=edge_gap)
        held_for = now - state.press_started_at if state.press_started_at is not None else 0.0
        return HotkeyEdgeTransition(state, "held", held_for=held_for)

    if state.is_down:
        held_for = now - state.press_started_at if state.press_started_at is not None else None
        if state.release_started_at is None:
            new_state = replace(state, release_started_at=now)
            return HotkeyEdgeTransition(new_state, "release_started", held_for=held_for)

        stable_for = now - state.release_started_at
        if stable_for >= release_stable_seconds:
            new_state = replace(
                state,
                is_down=False,
                press_started_at=None,
                release_started_at=None,
            )
            return HotkeyEdgeTransition(
                new_state,
                "rearmed",
                held_for=held_for,
                release_stable_for=stable_for,
            )
        return HotkeyEdgeTransition(
            state,
            "release_wait",
            held_for=held_for,
            release_stable_for=stable_for,
        )

    return HotkeyEdgeTransition(state, "idle")


@dataclass(frozen=True)
class HotkeyActionDecision:
    action: str
    ignore_for_seconds: float = 0.0


def decide_hotkey_action(
    *,
    now: float,
    ignore_until: float,
    start_in_progress: bool,
    is_recording: bool,
    finalizing: bool,
) -> HotkeyActionDecision:
    if now < ignore_until:
        return HotkeyActionDecision("ignore_debounce", ignore_until - now)
    if start_in_progress:
        return HotkeyActionDecision("ignore_start_in_progress")
    if is_recording:
        return HotkeyActionDecision("stop_recording")
    if finalizing:
        return HotkeyActionDecision("queue_start_after_finalizing")
    return HotkeyActionDecision("start_recording")
