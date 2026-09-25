"""Pure realtime timing, chunk-trigger and cancellation decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..config import (
    STREAM_SENTENCE_PAUSE_SECONDS_BALANCE,
    STREAM_SENTENCE_PAUSE_SECONDS_FAST,
    STREAM_SENTENCE_PAUSE_SECONDS_QUALITY,
)


@dataclass(frozen=True)
class RealtimeTimingPolicy:
    poll_interval: float
    min_seconds: float
    max_seconds: float
    trailing_silence_required: float
    sentence_end_pause_seconds: float
    streaming_vad_filter: bool
    max_bad_hold_seconds: float


@dataclass(frozen=True)
class ChunkCommitDecision:
    commit: bool
    advance_frame: bool
    has_pause: bool
    forced_commit: bool
    sentence_pause: bool
    pause_seconds: float
    reason: str


def build_realtime_timing(
    profile: str,
    configured_interval: float,
    *,
    use_vad_filter: bool,
    cpu_path_expected: bool,
) -> RealtimeTimingPolicy:
    interval = max(0.1, float(configured_interval))
    if profile == "Быстрее":
        poll_interval = 0.18
        min_seconds = min(max(0.75, interval * 0.35), 1.35)
        max_seconds = min(max(1.60, interval * 0.75), 2.80)
        trailing_silence_required = 0.24
        sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_FAST
    elif profile == "Баланс":
        poll_interval = 0.26
        min_seconds = min(max(1.35, interval * 0.55), 2.40)
        max_seconds = min(max(3.80, interval * 1.35), 6.00)
        trailing_silence_required = 0.42
        sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_BALANCE
    elif cpu_path_expected:
        poll_interval = 0.32
        min_seconds = min(max(1.80, interval * 0.60), 2.80)
        max_seconds = min(max(4.80, interval * 1.25), 7.00)
        trailing_silence_required = 0.55
        sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_QUALITY
    else:
        poll_interval = 0.24
        min_seconds = min(max(1.60, interval * 0.55), 2.60)
        max_seconds = min(max(4.20, interval * 1.15), 6.50)
        trailing_silence_required = 0.48
        sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_QUALITY

    return RealtimeTimingPolicy(
        poll_interval=poll_interval,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        trailing_silence_required=trailing_silence_required,
        sentence_end_pause_seconds=sentence_end_pause_seconds,
        streaming_vad_filter=bool(
            use_vad_filter or profile in {"Баланс", "Качество"}
        ),
        max_bad_hold_seconds=max(max_seconds + 3.0, max_seconds * 1.75),
    )


def is_probably_speech(stats: Mapping[str, float]) -> bool:
    return (
        float(stats.get("duration", 0.0)) >= 0.35
        and float(stats.get("peak", 0.0)) >= 0.010
        and float(stats.get("rms", 0.0)) >= 0.0025
    )


def decide_chunk_commit(
    stats: Mapping[str, float],
    timing: RealtimeTimingPolicy,
    *,
    is_final: bool = False,
) -> ChunkCommitDecision:
    duration = float(stats.get("duration", 0.0))
    peak = float(stats.get("peak", 0.0))
    rms = float(stats.get("rms", 0.0))
    pause_seconds = float(
        stats.get("pause_seconds", stats.get("trailing_silence", 0.0))
    )

    if is_final:
        if duration < 0.20 or peak < 0.008 or rms < 0.0018:
            return ChunkCommitDecision(
                commit=False,
                advance_frame=True,
                has_pause=False,
                forced_commit=False,
                sentence_pause=False,
                pause_seconds=pause_seconds,
                reason="final_too_short_or_quiet",
            )
        return ChunkCommitDecision(
            commit=True,
            advance_frame=False,
            has_pause=True,
            forced_commit=False,
            sentence_pause=True,
            pause_seconds=pause_seconds,
            reason="final_chunk",
        )

    if duration < timing.min_seconds:
        return ChunkCommitDecision(
            commit=False,
            advance_frame=False,
            has_pause=False,
            forced_commit=False,
            sentence_pause=False,
            pause_seconds=pause_seconds,
            reason="below_min_duration",
        )

    if not is_probably_speech(stats):
        return ChunkCommitDecision(
            commit=False,
            advance_frame=True,
            has_pause=False,
            forced_commit=False,
            sentence_pause=False,
            pause_seconds=pause_seconds,
            reason="not_probable_speech",
        )

    sentence_pause = pause_seconds >= timing.sentence_end_pause_seconds
    has_pause = pause_seconds >= timing.trailing_silence_required
    forced_commit = duration >= timing.max_seconds
    if not has_pause and not forced_commit:
        return ChunkCommitDecision(
            commit=False,
            advance_frame=False,
            has_pause=False,
            forced_commit=False,
            sentence_pause=sentence_pause,
            pause_seconds=pause_seconds,
            reason="awaiting_pause_or_max_duration",
        )

    return ChunkCommitDecision(
        commit=True,
        advance_frame=False,
        has_pause=has_pause,
        forced_commit=forced_commit,
        sentence_pause=sentence_pause,
        pause_seconds=pause_seconds,
        reason="pause" if has_pause else "max_duration",
    )


def should_process_final_chunk(
    *,
    stop_requested: bool,
    final_chunk_on_stop: bool,
) -> bool:
    return not (stop_requested and not final_chunk_on_stop)
