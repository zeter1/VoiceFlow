"""Typed worker-queue contracts and stale-session gates."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class WorkerMessageKind(str, Enum):
    GLOBAL_HOTKEY_PRESSED = "global_hotkey_pressed"
    EXTERNAL_TOGGLE_RECORDING = "external_toggle_recording"
    EXTERNAL_SHOW_WINDOW = "external_show_window"
    EXTERNAL_HIDE_WINDOW = "external_hide_window"
    EXTERNAL_EXIT = "external_exit"
    STREAM_RESULT = "stream_result"
    STREAM_WARNING = "stream_warning"


@dataclass(frozen=True)
class WorkerMessage:
    kind: WorkerMessageKind
    payload: object = None


@dataclass(frozen=True)
class HotkeyPressedPayload:
    hotkey: str
    target: object
    generation: object = None


@dataclass(frozen=True)
class ExternalTogglePayload:
    source: str
    target: object


@dataclass(frozen=True)
class StreamResultPayload:
    session_id: int
    raw: str
    cleaned: str
    origin: str
    stream_mode: str
    is_final: bool
    commit_meta: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamWarningPayload:
    session_id: int
    error: object


class StreamResultDisposition(str, Enum):
    ACCEPT = "accept"
    STALE_SESSION = "stale_session"
    AFTER_STOP = "after_stop"


class QueueWriter(Protocol):
    def put(self, item: object) -> None:
        ...


def put_worker_message(
    queue: QueueWriter,
    kind: WorkerMessageKind,
    payload: object = None,
) -> None:
    queue.put(WorkerMessage(kind=kind, payload=payload))


def coerce_worker_message(item: object) -> WorkerMessage:
    if isinstance(item, WorkerMessage):
        return item
    if isinstance(item, (tuple, list)) and len(item) == 2:
        kind_value, payload = item
        kind = (
            kind_value
            if isinstance(kind_value, WorkerMessageKind)
            else WorkerMessageKind(str(kind_value))
        )
        return WorkerMessage(kind=kind, payload=payload)
    raise ValueError(f"Unsupported worker message: {item!r}")


def classify_stream_result(
    *,
    message_session_id: int,
    current_session_id: int,
    recorder_active: bool,
    finalizing: bool,
) -> StreamResultDisposition:
    if int(message_session_id) != int(current_session_id):
        return StreamResultDisposition.STALE_SESSION
    if not recorder_active and not finalizing:
        return StreamResultDisposition.AFTER_STOP
    return StreamResultDisposition.ACCEPT


def is_current_session(message_session_id: int, current_session_id: int) -> bool:
    return int(message_session_id) == int(current_session_id)
