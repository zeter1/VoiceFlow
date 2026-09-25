"""Pure recording-state classification and toggle decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


BUSY_RECORDING_STATUSES = frozenset(
    {
        "Распознаю...",
        "Обрабатываю...",
        "Обрабатываю последний фрагмент...",
        "Идёт запись...",
        "Стриминг...",
        "Стриминг: предупреждение",
    }
)


class RecordingPhase(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STALE_IDLE = "stale_idle"


@dataclass(frozen=True)
class RecordingStateSnapshot:
    is_recording: bool
    finalizing: bool = False
    start_in_progress: bool = False
    pending_hotkey_start: bool = False
    streaming_thread_present: bool = False
    status: str = ""
    button_text: str = ""
    button_state: str = ""


def needs_idle_repair(snapshot: RecordingStateSnapshot) -> bool:
    if snapshot.is_recording:
        return False
    return (
        snapshot.finalizing
        or snapshot.start_in_progress
        or snapshot.pending_hotkey_start
        or snapshot.streaming_thread_present
        or snapshot.status in BUSY_RECORDING_STATUSES
        or snapshot.button_state == "disabled"
        or "Остановить" in snapshot.button_text
        or "Обрабатываю" in snapshot.button_text
    )


def derive_recording_phase(snapshot: RecordingStateSnapshot) -> RecordingPhase:
    if snapshot.is_recording:
        return RecordingPhase.RECORDING
    if snapshot.start_in_progress:
        return RecordingPhase.STARTING
    if needs_idle_repair(snapshot):
        return RecordingPhase.STALE_IDLE
    return RecordingPhase.IDLE


def recording_toggle_intent(snapshot: RecordingStateSnapshot) -> str:
    if snapshot.is_recording:
        return "stop_recording"
    if snapshot.start_in_progress:
        return "ignore_start_in_progress"
    if needs_idle_repair(snapshot):
        return "repair_then_start"
    return "start_recording"
