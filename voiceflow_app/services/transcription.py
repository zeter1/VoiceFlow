"""Local faster-whisper transcription service.

Extracted from the historical monolithic voiceflow.py without intentional
runtime behavior changes. Dependencies are imported from their focused owners.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time
import traceback
from typing import Any, Callable, Optional

from ..config import (
    LOCAL_WHISPER_MODEL,
    WHISPER_MODEL_OPTIONS,
)
from ..cuda_runtime import BackendRuntime, CudaRuntimeProbe
from ..dependencies import WhisperModel
from ..diagnostics import log_exception, log_info, log_warning


class LocalTranscriber:
    def __init__(
        self,
        *,
        backend_runtime: Optional[BackendRuntime] = None,
        model_factory: Optional[Callable[..., Any]] = None,
    ):
        self._backend_runtime = backend_runtime or CudaRuntimeProbe()
        self._model_factory = model_factory if model_factory is not None else WhisperModel
        self._models: dict[tuple[str, str, str], Any] = {}
        self._failed_backends: set[tuple[str, str, str]] = set()
        self._lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self.active_backend_label = "not loaded"

    def _normalize_model_name(self, model_name: str) -> str:
        return model_name if model_name in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL

    def _device_compute_candidates(self, device_option: str, compute_type_option: str) -> list[tuple[str, str]]:
        plan = self._backend_runtime.plan_candidates(device_option, compute_type_option)
        if plan.missing_runtime_dlls:
            log_warning(
                "CUDA dependencies are not available; using CPU backend",
                requested_device=device_option,
                requested_compute=compute_type_option,
                missing=list(plan.missing_runtime_dlls),
                hint="Install CUDA 12.x and add CUDA/cuDNN bin folders to PATH",
            )
        return list(plan.candidates)

    def _cuda_preflight_ok(self, model_name: str, cand_compute: str) -> tuple[bool, str]:
        result = self._backend_runtime.preflight(model_name, cand_compute)
        if result.cached:
            log_info(
                "CUDA runtime already verified; skipping repeated preflight",
                model=model_name,
                compute_type=cand_compute,
            )
        elif result.ok:
            log_info("CUDA preflight succeeded", model=model_name, compute_type=cand_compute)
        else:
            log_warning(
                "CUDA preflight failed",
                model=model_name,
                compute_type=cand_compute,
                returncode=result.returncode,
                details=result.details[-1200:],
            )
        return result.ok, result.details

    def _load_model(self, model_name: str, device: str = "auto", compute_type: str = "auto") -> Any:
        if self._model_factory is None:
            raise RuntimeError("Не установлен faster-whisper. Выполни: pip install faster-whisper")
        model_name = self._normalize_model_name(model_name)
        errors: list[str] = []
        for cand_device, cand_compute in self._device_compute_candidates(device, compute_type):
            key = (model_name, cand_device, cand_compute)
            if key in self._failed_backends:
                continue
            try:
                if cand_device == "cuda" and key not in self._models:
                    ok, details = self._cuda_preflight_ok(model_name, cand_compute)
                    if not ok:
                        self._failed_backends.add(key)
                        errors.append(f"{cand_device}/{cand_compute}: CUDA preflight failed: {details}")
                        log_warning(
                            "Skipping CUDA backend after failed preflight",
                            model=model_name,
                            device=cand_device,
                            compute_type=cand_compute,
                            details=details,
                        )
                        continue

                with self._lock:
                    if key not in self._models:
                        kwargs = {
                            "device": cand_device,
                            "compute_type": cand_compute,
                        }
                        if cand_device == "cpu":
                            # Ryzen 5 5600X has 6 cores / 12 threads; leave 1-2 threads for UI/system.
                            kwargs["cpu_threads"] = max(4, min(10, (os.cpu_count() or 8) - 1))
                        log_info("Loading Whisper model", model=model_name, device=cand_device, compute_type=cand_compute)
                        self._models[key] = self._model_factory(model_name, **kwargs)
                    self.active_backend_label = f"{cand_device}/{cand_compute}"
                    if cand_device == "cuda":
                        self._backend_runtime.mark_verified(cand_compute)
                    log_info("Whisper backend selected", model=model_name, device=cand_device, compute_type=cand_compute)
                    return self._models[key]
            except BaseException as exc:
                self._failed_backends.add(key)
                err_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                errors.append(f"{cand_device}/{cand_compute}: {err_text}")
                log_exception("Whisper backend load failed", exc, model=model_name, device=cand_device, compute_type=cand_compute)
                continue
        raise RuntimeError(
            "Не удалось загрузить Whisper-модель ни на одном backend. "
            "Если хочешь GPU, проверь CUDA/cuDNN. Подробности: " + " | ".join(errors[-3:])
        )

    def transcribe(
        self,
        wav_path: Path,
        language: str = "auto",
        model_name: str = LOCAL_WHISPER_MODEL,
        quality: str = "Максимальная точность",
        custom_terms: str = "",
        use_vad_filter: bool = True,
        context_text: str = "",
        device: str = "auto",
        compute_type: str = "auto",
        streaming: bool = False,
    ) -> str:
        """Transcribe audio with faster-whisper.

        For realtime chunks we intentionally use faster decoding parameters than
        the final/full-recording path. Beam 8 on large-v3 is too slow for live
        typing on a Ryzen 5 5600X; GPU + int8_float16 and beam 1-3 is the right
        low-latency compromise.
        """
        started_at = time.perf_counter()
        model = self._load_model(model_name, device=device, compute_type=compute_type)
        lang = None if language == "auto" else language
        beam_size, best_of, patience = self._quality_params(quality, streaming=streaming)
        initial_prompt = self._build_initial_prompt(language, custom_terms, context_text)

        vad_parameters = {
            # Realtime chunks from a distant microphone with PC/air-purifier noise
            # need a softer VAD than final full-recording mode. A high threshold
            # can cut quiet Russian consonants and make chunks look like silence,
            # while no VAD lets Whisper hallucinate "Продолжение следует" / subtitles.
            "threshold": 0.35 if streaming else 0.45,
            "min_speech_duration_ms": 80 if streaming else 160,
            "min_silence_duration_ms": 260 if streaming else 650,
            "speech_pad_ms": 360 if streaming else 420,
        }

        kwargs = dict(
            language=lang,
            vad_filter=use_vad_filter,
            vad_parameters=vad_parameters if use_vad_filter else None,
            beam_size=beam_size,
            best_of=best_of,
            patience=patience,
            temperature=0.0,
            condition_on_previous_text=False if streaming else True,
            initial_prompt=initial_prompt,
            no_speech_threshold=0.64 if streaming else 0.58,
            compression_ratio_threshold=2.45 if streaming else 2.6,
            log_prob_threshold=-1.0,
        )
        if streaming:
            # Supported by current faster-whisper; removed below if user has an older build.
            kwargs["without_timestamps"] = True

        with self._transcribe_lock:
            try:
                segments, _info = model.transcribe(str(wav_path), **kwargs)
            except TypeError:
                kwargs.pop("without_timestamps", None)
                segments, _info = model.transcribe(str(wav_path), **kwargs)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        result = re.sub(r"\s+", " ", text).strip()
        log_info(
            "Transcription completed",
            wav_path=wav_path,
            streaming=streaming,
            language=language,
            model=model_name,
            quality=quality,
            backend=self.active_backend_label,
            elapsed_seconds=round(time.perf_counter() - started_at, 3),
            result_chars=len(result),
        )
        return result

    def _quality_params(self, quality: str, streaming: bool = False) -> tuple[int, int, float]:
        if streaming:
            if quality == "Быстро":
                return 1, 1, 1.0
            if quality == "Точно":
                return 3, 3, 1.0
            # In the "Качество" realtime profile the user prefers fewer missed
            # words over maximum speed. Beam 5 is noticeably more stable for
            # noisy/distant microphones than beam 3, while still staying usable
            # on CUDA.
            return 5, 5, 1.10
        if quality == "Быстро":
            return 3, 3, 1.0
        if quality == "Точно":
            return 5, 5, 1.05
        return 8, 8, 1.15

    def _build_initial_prompt(self, language: str, custom_terms: str, context_text: str = "") -> str:
        base_ru = (
            "Это качественная диктовка на русском языке, иногда с английскими терминами. "
            "Нужно точно распознать естественную речь, имена, названия сервисов и технические термины. "
            "Не придумывай слова, которых нет в аудио. Не добавляй фразы вроде 'спасибо за просмотр'. "
            "Сохраняй смысл без добавления фактов. "
        )
        base_en = (
            "This is a high-quality voice dictation. Recognize natural speech, names, "
            "product names and technical terms accurately. Do not add new facts or filler outro phrases. "
        )
        base = base_en if language == "en" else base_ru
        terms = ", ".join(t.strip() for t in custom_terms.split(",") if t.strip())
        if terms:
            base += f"Особенно важно сохранить написание терминов: {terms}. "
        context = re.sub(r"\s+", " ", context_text or "").strip()
        if context:
            base += f"Предыдущий контекст диктовки: {context[-360:]}. "
        return base[:1000]
