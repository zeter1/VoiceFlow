"""Headless realtime worker engine.

Owns frame-cursor progression, realtime commit policy, transcription context,
temporary WAV cleanup and typed worker-message production. It intentionally has
no Tkinter or desktop API dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
from typing import Callable, Optional, Protocol

from ..core.realtime_policy import (
    RealtimeTimingPolicy,
    decide_chunk_commit,
    should_process_final_chunk,
)
from ..diagnostics import log_dictation_text, log_exception, log_info
from ..services.audio_analysis import stream_audio_stats
from ..services.contracts import AudioRecorderContract
from ..voice_commands import split_trailing_voice_control_command
from ..worker_messages import (
    QueueWriter,
    StreamResultPayload,
    StreamWarningPayload,
    WorkerMessageKind,
    put_worker_message,
)
from .session_controller import SessionTranscript, SessionTranscriptionRequest


AudioStatsFn = Callable[[list[object], int], dict[str, float]]
CleanChunkFn = Callable[[str, bool], tuple[str, str]]
DedupeChunkFn = Callable[[str, str], str]
SleepFn = Callable[[float], None]


class WaitEvent(Protocol):
    def wait(self, timeout: float) -> bool: ...
    def is_set(self) -> bool: ...


class ResetEvent(Protocol):
    def is_set(self) -> bool: ...
    def clear(self) -> None: ...


class RealtimeSessionPort(Protocol):
    def transcribe_frames(
        self,
        frames: list[object],
        request: SessionTranscriptionRequest,
        *,
        prefix: str = "stream_chunk",
        session_id: Optional[int] = None,
    ) -> Optional[SessionTranscript]: ...


@dataclass(frozen=True)
class RealtimeWorkerConfig:
    session_id: int
    origin: str
    mode: str
    language: str
    model_name: str
    quality: str
    custom_terms: str
    inference_device: str
    compute_type: str
    speed_profile: str
    timing: RealtimeTimingPolicy
    final_chunk_on_stop: bool


@dataclass(frozen=True)
class RealtimeWorkerSnapshot:
    last_frame_index: int
    committed_raw_context: str
    committed_clean_context: str
    bad_filtered_streak: int


class RealtimeWorkerEngine:
    def __init__(
        self,
        *,
        recorder: AudioRecorderContract,
        session_controller: RealtimeSessionPort,
        message_queue: QueueWriter,
        config: RealtimeWorkerConfig,
        start_frame_index: int,
        context_reset_event: ResetEvent,
        clean_chunk: CleanChunkFn,
        dedupe_chunk: DedupeChunkFn,
        audio_stats: AudioStatsFn = stream_audio_stats,
        sleep: SleepFn = time.sleep,
    ):
        self.recorder = recorder
        self.session_controller = session_controller
        self.message_queue = message_queue
        self.config = config
        self.context_reset_event = context_reset_event
        self.clean_chunk = clean_chunk
        self.dedupe_chunk = dedupe_chunk
        self.audio_stats = audio_stats
        self.sleep = sleep
        self.last_frame_index = int(start_frame_index)
        self.committed_raw_context = ""
        self.committed_clean_context = ""
        self.bad_filtered_streak = 0

    def snapshot(self) -> RealtimeWorkerSnapshot:
        return RealtimeWorkerSnapshot(
            last_frame_index=self.last_frame_index,
            committed_raw_context=self.committed_raw_context,
            committed_clean_context=self.committed_clean_context,
            bad_filtered_streak=self.bad_filtered_streak,
        )

    def _reset_context_if_requested(self) -> None:
        if not self.context_reset_event.is_set():
            return
        self.committed_raw_context = ""
        self.committed_clean_context = ""
        self.context_reset_event.clear()
        log_info("Realtime dictation context reset", session_id=self.config.session_id)

    def _emit_warning(self, exc: BaseException) -> None:
        try:
            put_worker_message(
                self.message_queue,
                WorkerMessageKind.STREAM_WARNING,
                StreamWarningPayload(session_id=self.config.session_id, error=exc),
            )
        except Exception:
            pass

    def _transcription_request(self) -> SessionTranscriptionRequest:
        return SessionTranscriptionRequest(
            language=self.config.language,
            model_name=self.config.model_name,
            quality=self.config.quality,
            custom_terms=self.config.custom_terms,
            use_vad_filter=self.config.timing.streaming_vad_filter,
            context_text=self.committed_raw_context[-500:],
            device=self.config.inference_device,
            compute_type=self.config.compute_type,
            streaming=True,
            clean_output=False,
        )

    def process_frames(
        self,
        frames: list[object],
        new_index: int,
        sample_rate: int,
        *,
        is_final: bool = False,
    ) -> None:
        self._reset_context_if_requested()
        if not frames:
            self.last_frame_index = int(new_index)
            return

        stats = self.audio_stats(frames, sample_rate)
        decision = decide_chunk_commit(stats, self.config.timing, is_final=is_final)
        if not decision.commit:
            if decision.advance_frame:
                self.last_frame_index = int(new_index)
            return

        duration = float(stats["duration"])
        pause_seconds = decision.pause_seconds
        sentence_pause = decision.sentence_pause
        has_pause = decision.has_pause
        forced_commit = decision.forced_commit
        sentence_end_pause_seconds = self.config.timing.sentence_end_pause_seconds

        if not is_final:
            log_info(
                "Realtime commit triggered",
                session_id=self.config.session_id,
                duration=round(duration, 3),
                trailing_silence=round(float(stats["trailing_silence"]), 3),
                speech_trailing_silence=round(float(stats.get("speech_trailing_silence", 0.0)), 3),
                pause_seconds=round(pause_seconds, 3),
                sentence_pause=sentence_pause,
                sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                forced_commit=forced_commit,
                has_pause=has_pause,
                frame_index=new_index,
                peak=round(float(stats.get("peak", 0.0)), 5),
                rms=round(float(stats.get("rms", 0.0)), 5),
                peak_to_rms=round(float(stats.get("peak_to_rms", 0.0)), 3),
                active_ratio=round(float(stats.get("active_ratio", 0.0)), 3),
                streaming_vad_filter=self.config.timing.streaming_vad_filter,
                decision_reason=decision.reason,
            )

        wav_path: Optional[Path] = None
        try:
            transcript = self.session_controller.transcribe_frames(
                frames,
                self._transcription_request(),
                prefix="stream_stable_chunk",
                session_id=self.config.session_id,
            )
            if transcript is None:
                self.last_frame_index = int(new_index)
                return

            wav_path = transcript.wav_path
            transcribed_raw = transcript.raw_text
            raw_text_for_end = (transcribed_raw or "").strip()
            raw_had_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text_for_end))
            whisper_sentence_end = bool(
                re.search(r"[.!?][\"'»\)\]]*$", raw_text_for_end)
            ) and not raw_had_ellipsis
            keep_sentence_end = bool(is_final or sentence_pause or whisper_sentence_end)
            raw, cleaned = self.clean_chunk(transcribed_raw, keep_sentence_end)

            if not raw and not cleaned:
                self.bad_filtered_streak += 1
                log_dictation_text(
                    "stream_filtered",
                    session_id=self.config.session_id,
                    origin=self.config.origin,
                    mode=self.config.mode,
                    raw_text=transcribed_raw,
                    reason="bad_or_empty_stream_text",
                    model=self.config.model_name,
                    language=self.config.language,
                    device=self.config.inference_device,
                    compute_type=self.config.compute_type,
                    speed_profile=self.config.speed_profile,
                    is_final=is_final,
                    has_pause=has_pause,
                    forced_commit=forced_commit,
                    audio_duration=round(duration, 3),
                    trailing_silence=round(float(stats["trailing_silence"]), 3),
                    speech_trailing_silence=round(float(stats.get("speech_trailing_silence", 0.0)), 3),
                    pause_seconds=round(pause_seconds, 3),
                    sentence_pause=bool(sentence_pause),
                    sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                    whisper_sentence_end=bool(whisper_sentence_end),
                    keep_sentence_end=bool(keep_sentence_end),
                    peak=round(float(stats.get("peak", 0.0)), 5),
                    rms=round(float(stats.get("rms", 0.0)), 5),
                    peak_to_rms=round(float(stats.get("peak_to_rms", 0.0)), 3),
                    active_ratio=round(float(stats.get("active_ratio", 0.0)), 3),
                    streaming_vad_filter=self.config.timing.streaming_vad_filter,
                    bad_filtered_streak=self.bad_filtered_streak,
                    retain_audio_for_retry=False,
                    retry_after_frame_index=None,
                )
                log_info(
                    "Realtime chunk filtered as hallucination/noise",
                    session_id=self.config.session_id,
                    raw_text=transcribed_raw,
                    retain_audio_for_retry=False,
                    bad_filtered_streak=self.bad_filtered_streak,
                    duration=round(duration, 3),
                    peak=round(float(stats.get("peak", 0.0)), 5),
                    rms=round(float(stats.get("rms", 0.0)), 5),
                    active_ratio=round(float(stats.get("active_ratio", 0.0)), 3),
                )
                self.last_frame_index = int(new_index)
                return

            raw_delta = self.dedupe_chunk(self.committed_raw_context, raw)
            clean_delta = self.dedupe_chunk(self.committed_clean_context, cleaned)
            if raw_delta or clean_delta:
                if not raw_delta:
                    raw_delta = clean_delta
                if not clean_delta:
                    clean_delta = raw_delta

                raw_context_delta, raw_context_command = split_trailing_voice_control_command(raw_delta)
                clean_context_delta, clean_context_command = split_trailing_voice_control_command(clean_delta)
                if raw_context_command is None:
                    raw_context_delta = raw_delta
                if clean_context_command is None:
                    clean_context_delta = clean_delta
                if raw_context_delta:
                    self.committed_raw_context = (
                        self.committed_raw_context + " " + raw_context_delta
                    ).strip()
                if clean_context_delta:
                    self.committed_clean_context = (
                        self.committed_clean_context + " " + clean_context_delta
                    ).strip()

                commit_meta = {
                    "pause_seconds": pause_seconds,
                    "trailing_silence": float(stats.get("trailing_silence", 0.0)),
                    "speech_trailing_silence": float(stats.get("speech_trailing_silence", 0.0)),
                    "sentence_pause": bool(sentence_pause),
                    "sentence_end_pause_seconds": float(sentence_end_pause_seconds),
                    "has_pause": bool(has_pause),
                    "forced_commit": bool(forced_commit),
                    "whisper_sentence_end": bool(whisper_sentence_end),
                    "keep_sentence_end": bool(keep_sentence_end),
                    "raw_text": raw_delta,
                }
                log_dictation_text(
                    "stream_chunk",
                    session_id=self.config.session_id,
                    origin=self.config.origin,
                    mode=self.config.mode,
                    raw_text=raw_delta,
                    cleaned_text=clean_delta,
                    full_clean_context=self.committed_clean_context[-1200:],
                    model=self.config.model_name,
                    language=self.config.language,
                    device=self.config.inference_device,
                    compute_type=self.config.compute_type,
                    speed_profile=self.config.speed_profile,
                    is_final=is_final,
                    has_pause=has_pause,
                    forced_commit=forced_commit,
                    audio_duration=round(duration, 3),
                    trailing_silence=round(float(stats["trailing_silence"]), 3),
                    speech_trailing_silence=round(float(stats.get("speech_trailing_silence", 0.0)), 3),
                    pause_seconds=round(pause_seconds, 3),
                    sentence_pause=bool(sentence_pause),
                    sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                    whisper_sentence_end=bool(whisper_sentence_end),
                    keep_sentence_end=bool(keep_sentence_end),
                    peak=round(float(stats.get("peak", 0.0)), 5),
                    rms=round(float(stats.get("rms", 0.0)), 5),
                    peak_to_rms=round(float(stats.get("peak_to_rms", 0.0)), 3),
                    active_ratio=round(float(stats.get("active_ratio", 0.0)), 3),
                    streaming_vad_filter=self.config.timing.streaming_vad_filter,
                )
                self.bad_filtered_streak = 0
                put_worker_message(
                    self.message_queue,
                    WorkerMessageKind.STREAM_RESULT,
                    StreamResultPayload(
                        session_id=self.config.session_id,
                        raw=raw_delta,
                        cleaned=clean_delta,
                        origin=self.config.origin,
                        stream_mode=self.config.mode,
                        is_final=is_final,
                        commit_meta=commit_meta,
                    ),
                )
            self.last_frame_index = int(new_index)
        finally:
            if wav_path is not None:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def run(self, stop_event: WaitEvent) -> RealtimeWorkerSnapshot:
        timing = self.config.timing
        log_info(
            "Realtime timing configured",
            session_id=self.config.session_id,
            profile=self.config.speed_profile,
            model=self.config.model_name,
            poll_interval=timing.poll_interval,
            min_seconds=round(timing.min_seconds, 2),
            max_seconds=round(timing.max_seconds, 2),
            trailing_silence_required=round(timing.trailing_silence_required, 2),
            sentence_end_pause_seconds=round(timing.sentence_end_pause_seconds, 2),
            streaming_vad_filter=timing.streaming_vad_filter,
            max_bad_hold_seconds=round(timing.max_bad_hold_seconds, 2),
        )

        while not stop_event.wait(timing.poll_interval):
            if not self.recorder.is_recording:
                break
            try:
                self._reset_context_if_requested()
                frames, new_index, sample_rate = self.recorder.get_frames_since(self.last_frame_index)
                self.process_frames(frames, new_index, sample_rate, is_final=False)
            except BaseException as exc:
                log_exception(
                    "Realtime streaming chunk failed",
                    exc,
                    session_id=self.config.session_id,
                    origin=self.config.origin,
                    mode=self.config.mode,
                )
                self._emit_warning(exc)
                self.sleep(0.8)

        if not should_process_final_chunk(
            stop_requested=stop_event.is_set(),
            final_chunk_on_stop=self.config.final_chunk_on_stop,
        ):
            log_info(
                "Realtime final chunk skipped to keep hotkey responsive",
                session_id=self.config.session_id,
                origin=self.config.origin,
                mode=self.config.mode,
            )
            log_dictation_text(
                "stream_final_skipped",
                session_id=self.config.session_id,
                origin=self.config.origin,
                mode=self.config.mode,
                reason="stop_should_not_block_next_hotkey",
            )
            log_info(
                "Realtime streaming worker finished",
                session_id=self.config.session_id,
                origin=self.config.origin,
                mode=self.config.mode,
            )
            return self.snapshot()

        try:
            frames, new_index, sample_rate = self.recorder.get_frames_since(self.last_frame_index)
            self.process_frames(frames, new_index, sample_rate, is_final=True)
        except BaseException as exc:
            log_exception(
                "Realtime final chunk failed",
                exc,
                session_id=self.config.session_id,
                origin=self.config.origin,
                mode=self.config.mode,
            )
            self._emit_warning(exc)

        log_info(
            "Realtime streaming worker finished",
            session_id=self.config.session_id,
            origin=self.config.origin,
            mode=self.config.mode,
        )
        return self.snapshot()
