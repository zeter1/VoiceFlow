"""Structural service contracts used by application orchestration.

These Protocols import only the standard library, so architecture/contract tests
can run without microphone, Whisper, CUDA or GUI dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class AudioRecorderContract(Protocol):
    device: Optional[int]
    sample_rate: int
    channels: int

    @property
    def is_recording(self) -> bool:
        ...

    def start(self) -> None:
        ...

    def stop_discard(self) -> None:
        ...

    def stop_stream_keep_frames(self) -> None:
        ...

    def discard_frames(self) -> None:
        ...

    def stop_to_wav(self) -> Path:
        ...

    def frames_count(self) -> int:
        ...

    def get_frames_since(self, frame_index: int) -> tuple[list[object], int, int]:
        ...

    def frames_to_wav(self, frames: list[object], prefix: str = "stream_chunk") -> Optional[Path]:
        ...


@runtime_checkable
class TranscriberContract(Protocol):
    active_backend_label: str

    def backend_candidates(
        self,
        device_option: str,
        compute_type_option: str,
    ) -> list[tuple[str, str]]:
        ...

    def transcribe(
        self,
        wav_path: Path,
        language: str = "auto",
        model_name: str = "small",
        quality: str = "Максимальная точность",
        custom_terms: str = "",
        use_vad_filter: bool = True,
        context_text: str = "",
        device: str = "auto",
        compute_type: str = "auto",
        streaming: bool = False,
    ) -> str:
        ...


@runtime_checkable
class TextCleanerContract(Protocol):
    def clean(
        self,
        raw_text: str,
        mode: str,
        language: str,
        custom_terms: str = "",
        deep_grammar: bool = True,
    ) -> str:
        ...
