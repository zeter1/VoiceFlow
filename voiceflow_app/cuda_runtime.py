"""CUDA/environment adapter for transcription backend selection."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys
from typing import Callable, Optional, Protocol

from .config import (
    COMPUTE_TYPE_OPTIONS,
    CUDA_REQUIRED_WINDOWS_DLLS,
    INFERENCE_DEVICE_OPTIONS,
    IS_WINDOWS,
)


DllFinder = Callable[[str], Optional[str]]
ProcessRunner = Callable[..., object]


@dataclass(frozen=True)
class BackendCandidatePlan:
    candidates: tuple[tuple[str, str], ...]
    missing_runtime_dlls: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackendPreflightResult:
    ok: bool
    details: str = ""
    cached: bool = False
    returncode: Optional[int] = None


class BackendRuntime(Protocol):
    def plan_candidates(self, device_option: str, compute_type_option: str) -> BackendCandidatePlan:
        ...

    def preflight(self, model_name: str, compute_type: str) -> BackendPreflightResult:
        ...

    def mark_verified(self, compute_type: str) -> None:
        ...


def select_backend_candidates(
    device_option: str,
    compute_type_option: str,
    *,
    cuda_available: bool,
) -> tuple[tuple[str, str], ...]:
    device = device_option if device_option in INFERENCE_DEVICE_OPTIONS else "auto"
    compute = compute_type_option if compute_type_option in COMPUTE_TYPE_OPTIONS else "auto"
    cpu_compute = compute if compute in {"int8", "float32"} else "int8"

    if device == "cpu":
        return (("cpu", cpu_compute),)

    if not cuda_available:
        return (("cpu", cpu_compute),)

    if device == "cuda":
        if compute == "auto":
            return (
                ("cuda", "int8_float16"),
                ("cuda", "float16"),
                ("cuda", "int8"),
                ("cpu", "int8"),
            )
        return (("cuda", compute), ("cpu", "int8"))

    if compute == "auto":
        return (
            ("cuda", "int8_float16"),
            ("cuda", "float16"),
            ("cuda", "int8"),
            ("cpu", "int8"),
        )
    return (("cuda", compute), ("cpu", "int8"))


def _default_dll_finder(name: str) -> Optional[str]:
    from .diagnostics import find_windows_dll

    path = find_windows_dll(name)
    return str(path) if path is not None else None


class CudaRuntimeProbe:
    def __init__(
        self,
        *,
        is_windows: bool = IS_WINDOWS,
        dll_finder: Optional[DllFinder] = None,
        process_runner: Optional[ProcessRunner] = None,
        executable: Optional[str] = None,
    ):
        self.is_windows = is_windows
        self._dll_finder = dll_finder or _default_dll_finder
        self._process_runner = process_runner or subprocess.run
        self._executable = executable or sys.executable
        self._verified_compute_types: set[str] = set()

    def missing_runtime_dlls(self) -> tuple[str, ...]:
        if not self.is_windows:
            return ()
        return tuple(
            dll_name
            for dll_name in CUDA_REQUIRED_WINDOWS_DLLS
            if self._dll_finder(dll_name) is None
        )

    def plan_candidates(self, device_option: str, compute_type_option: str) -> BackendCandidatePlan:
        normalized_device = device_option if device_option in INFERENCE_DEVICE_OPTIONS else "auto"
        if normalized_device == "cpu":
            return BackendCandidatePlan(
                select_backend_candidates(device_option, compute_type_option, cuda_available=False)
            )

        missing = self.missing_runtime_dlls()
        return BackendCandidatePlan(
            select_backend_candidates(
                device_option,
                compute_type_option,
                cuda_available=not missing,
            ),
            missing_runtime_dlls=missing,
        )

    def mark_verified(self, compute_type: str) -> None:
        self._verified_compute_types.add(compute_type)

    def preflight(self, model_name: str, compute_type: str) -> BackendPreflightResult:
        if compute_type in self._verified_compute_types:
            return BackendPreflightResult(True, cached=True)

        missing = self.missing_runtime_dlls()
        if missing:
            return BackendPreflightResult(
                False,
                "Missing CUDA 12 runtime DLLs in PATH: "
                + ", ".join(missing)
                + "; skipping slow CUDA preflight",
            )

        code = (
            "import os, tempfile, wave\n"
            "from faster_whisper import WhisperModel\n"
            f"m = WhisperModel({model_name!r}, device='cuda', compute_type={compute_type!r})\n"
            "p = os.path.join(tempfile.gettempdir(), 'voiceflow_cuda_preflight.wav')\n"
            "with wave.open(p, 'wb') as wf:\n"
            "    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(b'\\x00\\x00' * 16000)\n"
            "segments, info = m.transcribe(p, language='ru', beam_size=1, best_of=1, vad_filter=False, without_timestamps=True)\n"
            "list(segments)\n"
            "print('VOICEFLOW_CUDA_OK')\n"
        )
        try:
            result = self._process_runner(
                [self._executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
            returncode = int(getattr(result, "returncode", 1))
            stdout = str(getattr(result, "stdout", "") or "")
            stderr = str(getattr(result, "stderr", "") or "")
            if returncode == 0 and "VOICEFLOW_CUDA_OK" in stdout:
                self.mark_verified(compute_type)
                return BackendPreflightResult(True, returncode=returncode)
            details = (stderr or stdout or "unknown CUDA load error").strip()[-1200:]
            return BackendPreflightResult(False, details=details, returncode=returncode)
        except subprocess.TimeoutExpired:
            return BackendPreflightResult(
                False,
                "CUDA preflight timeout: модель слишком долго загружалась в тестовом процессе",
            )
        except Exception as exc:
            return BackendPreflightResult(False, f"{type(exc).__name__}: {exc}")
