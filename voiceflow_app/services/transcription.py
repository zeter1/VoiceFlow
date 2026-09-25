"""Local faster-whisper transcription service.

Extracted from the historical monolithic voiceflow.py without intentional
runtime behavior changes. Shared runtime dependencies live in voiceflow_app.runtime.
"""

from __future__ import annotations

from ..runtime import *  # noqa: F401,F403 - transitional compatibility namespace


class LocalTranscriber:
    def __init__(self):
        self._models: dict[tuple[str, str, str], WhisperModel] = {}
        self._failed_backends: set[tuple[str, str, str]] = set()
        self._verified_cuda_compute_types: set[str] = set()
        self._lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self.active_backend_label = "not loaded"

    def _normalize_model_name(self, model_name: str) -> str:
        return model_name if model_name in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL

    def _device_compute_candidates(self, device_option: str, compute_type_option: str) -> list[tuple[str, str]]:
        device_option = device_option if device_option in INFERENCE_DEVICE_OPTIONS else "auto"
        compute_type_option = compute_type_option if compute_type_option in COMPUTE_TYPE_OPTIONS else "auto"
        cpu_compute = compute_type_option if compute_type_option in {"int8", "float32"} else "int8"

        if device_option == "cpu":
            # Some GPU compute types are invalid on CPU. Force a safe CPU backend.
            return [("cpu", cpu_compute)]

        missing_cuda_dlls = self._windows_missing_cuda_dlls()
        if missing_cuda_dlls:
            log_warning(
                "CUDA dependencies are not available; using CPU backend",
                requested_device=device_option,
                requested_compute=compute_type_option,
                required=list(CUDA_REQUIRED_WINDOWS_DLLS),
                missing=missing_cuda_dlls,
                hint="Install CUDA 12.x and add CUDA/cuDNN bin folders to PATH",
            )
            return [("cpu", cpu_compute)]

        if device_option == "cuda":
            # Even when the user explicitly selects CUDA, keep CPU as a safe fallback.
            # Without this, missing cuDNN/cuBLAS or an incompatible compute type can close/crash the app.
            if compute_type_option == "auto":
                return [("cuda", "int8_float16"), ("cuda", "float16"), ("cuda", "int8"), ("cpu", "int8")]
            return [("cuda", compute_type_option), ("cpu", "int8")]

        # auto: try GPU first, then gracefully fall back to CPU if CUDA/cuDNN is absent.
        if compute_type_option == "auto":
            return [("cuda", "int8_float16"), ("cuda", "float16"), ("cuda", "int8"), ("cpu", "int8")]
        return [("cuda", compute_type_option), ("cpu", "int8")]

    def _windows_dll_available(self, dll_name: str) -> bool:
        return not IS_WINDOWS or find_windows_dll(dll_name) is not None

    def _windows_missing_cuda_dlls(self) -> list[str]:
        if not IS_WINDOWS:
            return []
        return [dll_name for dll_name in CUDA_REQUIRED_WINDOWS_DLLS if find_windows_dll(dll_name) is None]

    def _cuda_preflight_ok(self, model_name: str, cand_compute: str) -> tuple[bool, str]:
        """Test CUDA model loading in a child process before using it in the UI process.

        Some broken CUDA/cuDNN/cuBLAS installations do not raise a normal Python
        exception; the native library can terminate python.exe. Running the first
        CUDA probe in a subprocess prevents the whole VoiceFlow window from closing.
        """
        if cand_compute in self._verified_cuda_compute_types:
            log_info(
                "CUDA runtime already verified; skipping repeated preflight",
                model=model_name,
                compute_type=cand_compute,
            )
            return True, ""

        missing_cuda_dlls = self._windows_missing_cuda_dlls()
        if missing_cuda_dlls:
            details = (
                "Missing CUDA 12 runtime DLLs in PATH: "
                + ", ".join(missing_cuda_dlls)
                + "; skipping slow CUDA preflight"
            )
            log_warning(
                "CUDA dependency missing; skipping CUDA preflight",
                model=model_name,
                compute_type=cand_compute,
                required=list(CUDA_REQUIRED_WINDOWS_DLLS),
                missing=missing_cuda_dlls,
                details=details,
            )
            return False, details

        log_info("CUDA preflight started", model=model_name, compute_type=cand_compute)
        code = (
            "import os, tempfile, wave\n"
            "from faster_whisper import WhisperModel\n"
            f"m = WhisperModel({model_name!r}, device='cuda', compute_type={cand_compute!r})\n"
            "p = os.path.join(tempfile.gettempdir(), 'voiceflow_cuda_preflight.wav')\n"
            "with wave.open(p, 'wb') as wf:\n"
            "    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(b'\\x00\\x00' * 16000)\n"
            "segments, info = m.transcribe(p, language='ru', beam_size=1, best_of=1, vad_filter=False, without_timestamps=True)\n"
            "list(segments)\n"
            "print('VOICEFLOW_CUDA_OK')\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
            if result.returncode == 0 and "VOICEFLOW_CUDA_OK" in result.stdout:
                self._verified_cuda_compute_types.add(cand_compute)
                log_info("CUDA preflight succeeded", model=model_name, compute_type=cand_compute)
                return True, ""
            details = (result.stderr or result.stdout or "unknown CUDA load error").strip()
            log_warning(
                "CUDA preflight failed",
                model=model_name,
                compute_type=cand_compute,
                returncode=result.returncode,
                details=details[-1200:],
            )
            return False, details[-1200:]
        except subprocess.TimeoutExpired:
            log_warning("CUDA preflight timed out", model=model_name, compute_type=cand_compute)
            return False, "CUDA preflight timeout: модель слишком долго загружалась в тестовом процессе"
        except Exception as exc:
            log_exception("CUDA preflight crashed", exc, model=model_name, compute_type=cand_compute)
            return False, str(exc)

    def _load_model(self, model_name: str, device: str = "auto", compute_type: str = "auto") -> WhisperModel:
        if WhisperModel is None:
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
                        self._models[key] = WhisperModel(model_name, **kwargs)
                    self.active_backend_label = f"{cand_device}/{cand_compute}"
                    if cand_device == "cuda":
                        self._verified_cuda_compute_types.add(cand_compute)
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
