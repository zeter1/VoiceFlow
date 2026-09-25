"""Headless application-session orchestration.

Owns capture session identity, capture lifecycle and committed realtime text.
It depends on service contracts only and can be exercised without Tk, a
microphone, Whisper model, CUDA or Windows desktop APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Optional

from ..services.contracts import (
    AudioRecorderContract,
    TextCleanerContract,
    TranscriberContract,
)


class SessionPhase(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    FINALIZING = "finalizing"


class SessionTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: int
    phase: SessionPhase
    committed_text: str
    inserted_any: bool
    last_error: str


@dataclass(frozen=True)
class SessionTranscriptionRequest:
    language: str = "auto"
    model_name: str = "small"
    quality: str = "Максимальная точность"
    custom_terms: str = ""
    use_vad_filter: bool = True
    context_text: str = ""
    device: str = "auto"
    compute_type: str = "auto"
    streaming: bool = True
    clean_mode: str = "Чистый текст"
    deep_grammar: bool = False
    clean_output: bool = True


@dataclass(frozen=True)
class SessionTranscript:
    session_id: int
    wav_path: Path
    raw_text: str
    cleaned_text: str


class HeadlessSessionController:
    def __init__(
        self,
        recorder: AudioRecorderContract,
        transcriber: TranscriberContract,
        cleaner: TextCleanerContract,
    ):
        self.recorder = recorder
        self.transcriber = transcriber
        self.cleaner = cleaner
        self._session_id = 0
        self._phase = SessionPhase.IDLE
        self._committed_text = ""
        self._inserted_any = False
        self._last_error = ""

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def phase(self) -> SessionPhase:
        return self._phase

    @property
    def committed_text(self) -> str:
        return self._committed_text

    @property
    def inserted_any(self) -> bool:
        return self._inserted_any

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            session_id=self._session_id,
            phase=self._phase,
            committed_text=self._committed_text,
            inserted_any=self._inserted_any,
            last_error=self._last_error,
        )

    def begin_capture(self) -> int:
        if self._phase is not SessionPhase.IDLE or self.recorder.is_recording:
            raise SessionTransitionError(
                f"Cannot start capture from phase={self._phase.value}, "
                f"recorder_active={bool(self.recorder.is_recording)}"
            )
        self._phase = SessionPhase.STARTING
        self._session_id += 1
        self.reset_commits()
        self._last_error = ""
        return self._session_id

    def activate_capture(self) -> None:
        if self._phase is not SessionPhase.STARTING:
            raise SessionTransitionError(f"Cannot activate capture from phase={self._phase.value}")
        try:
            self.recorder.start()
        except BaseException as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self.recover_idle()
            raise
        self._phase = SessionPhase.RECORDING

    def start_capture(self) -> int:
        session_id = self.begin_capture()
        self.activate_capture()
        return session_id

    def begin_finalizing(self) -> None:
        if self._phase is SessionPhase.RECORDING:
            self._phase = SessionPhase.FINALIZING
        elif self._phase is not SessionPhase.FINALIZING:
            raise SessionTransitionError(f"Cannot finalize from phase={self._phase.value}")

    def stop_capture(self, *, discard_frames: bool = True) -> None:
        if self._phase is SessionPhase.IDLE and not self.recorder.is_recording:
            if discard_frames:
                try:
                    self.recorder.discard_frames()
                except Exception:
                    pass
            return

        if self._phase is SessionPhase.STARTING:
            raise SessionTransitionError("Cannot stop while capture start is incomplete")

        if self._phase is SessionPhase.RECORDING:
            self._phase = SessionPhase.FINALIZING

        try:
            if self.recorder.is_recording:
                self.recorder.stop_stream_keep_frames()
            if discard_frames:
                self.recorder.discard_frames()
        except BaseException as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self.recover_idle()
            raise
        self._phase = SessionPhase.IDLE

    def abort_start(self) -> None:
        if self._phase is SessionPhase.STARTING:
            self.recover_idle()

    def recover_idle(self) -> None:
        try:
            if self.recorder.is_recording:
                self.recorder.stop_discard()
            else:
                self.recorder.discard_frames()
        except Exception:
            try:
                self.recorder.stop_stream_keep_frames()
            except Exception:
                pass
            try:
                self.recorder.discard_frames()
            except Exception:
                pass
        self._phase = SessionPhase.IDLE

    def reset_commits(self) -> None:
        self._committed_text = ""
        self._inserted_any = False

    def record_commit(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return self._committed_text
        self._committed_text = (
            f"{self._committed_text} {normalized}".strip()
            if self._committed_text
            else normalized
        )
        self._inserted_any = True
        return self._committed_text

    def transcribe_frames(
        self,
        frames: list[object],
        request: SessionTranscriptionRequest,
        *,
        prefix: str = "stream_chunk",
        session_id: Optional[int] = None,
    ) -> Optional[SessionTranscript]:
        wav_path = self.recorder.frames_to_wav(frames, prefix=prefix)
        if wav_path is None:
            return None

        raw = self.transcriber.transcribe(
            wav_path,
            request.language,
            model_name=request.model_name,
            quality=request.quality,
            custom_terms=request.custom_terms,
            use_vad_filter=request.use_vad_filter,
            context_text=request.context_text,
            device=request.device,
            compute_type=request.compute_type,
            streaming=request.streaming,
        )
        cleaned = raw
        if request.clean_output:
            cleaned = self.cleaner.clean(
                raw,
                request.clean_mode,
                request.language,
                custom_terms=request.custom_terms,
                deep_grammar=request.deep_grammar,
            )
        return SessionTranscript(
            session_id=self._session_id if session_id is None else session_id,
            wav_path=wav_path,
            raw_text=raw,
            cleaned_text=cleaned,
        )
