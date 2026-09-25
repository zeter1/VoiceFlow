"""Recording lifecycle and transcriber warm-up.

Methods keep their original implementation while the physical module boundary
makes navigation, review and future extraction safer.
"""

from __future__ import annotations

from ..context import *  # noqa: F401,F403 - compatibility surface for extracted methods


class RecordingMixin:
    def _is_streaming_thread_alive(self) -> bool:
        try:
            return bool(self.streaming_thread is not None and self.streaming_thread.is_alive())
        except Exception:
            return False

    def _repair_idle_recording_state(self, reason: str) -> bool:
        """Force the UI/state back to idle when no microphone recording is active.

        This is a safety net for the recurring bug where both F9 and the tray
        menu appear to stop working after a stop. If the recorder is already
        stopped, the next toggle must be allowed to start even if a stale
        streaming thread, disabled button, old finalizing flag or busy status is
        still present.
        """
        try:
            if self.recorder.is_recording:
                return False

            status = self.status_var.get()
            try:
                button_text = str(self.record_btn.cget("text"))
                button_state = str(self.record_btn.cget("state"))
            except Exception:
                button_text = ""
                button_state = ""
            stream_alive = self._is_streaming_thread_alive()
            busy_status = status in {
                "Распознаю...",
                "Обрабатываю...",
                "Обрабатываю последний фрагмент...",
                "Идёт запись...",
                "Стриминг...",
                "Стриминг: предупреждение",
            }
            needs_repair = (
                bool(self.finalizing_recording)
                or bool(self.recording_start_in_progress)
                or bool(self.pending_hotkey_start_requested)
                or self.streaming_thread is not None
                or busy_status
                or button_state == "disabled"
                or ("Остановить" in button_text)
                or ("Обрабатываю" in button_text)
            )
            if not needs_repair:
                return False

            log_warning(
                "Recording state repaired before toggle",
                reason=reason,
                status=status,
                button_text=button_text,
                button_state=button_state,
                stream_alive=stream_alive,
                finalizing=self.finalizing_recording,
                recording_start_in_progress=self.recording_start_in_progress,
                pending_hotkey_start=self.pending_hotkey_start_requested,
                session_id=self.recording_session_id,
            )
            self.log_state(
                "recording",
                "idle_state_repaired",
                reason=reason,
                previous_status=status,
                previous_button_text=button_text,
                previous_button_state=button_state,
                previous_stream_alive=stream_alive,
            )
            try:
                self.log_hotkey_trace(
                    "idle_state_repaired",
                    reason=reason,
                    previous_status=status,
                    previous_button_text=button_text,
                    previous_button_state=button_state,
                    previous_stream_alive=stream_alive,
                )
            except Exception:
                pass

            try:
                self.streaming_stop_event.set()
            except Exception:
                pass
            try:
                self.recorder.discard_frames()
            except Exception:
                pass
            try:
                if self.timer_job:
                    self.root.after_cancel(self.timer_job)
                    self.timer_job = None
            except Exception:
                pass

            self.finalizing_recording = False
            self.recording_start_in_progress = False
            self.pending_hotkey_start_requested = False
            self.pending_hotkey_start_target = None
            self.streaming_thread = None
            self.last_wav_path = None
            self.record_started_at = None
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            try:
                self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            except Exception:
                pass
            try:
                if getattr(self.notifications, "_last_kind", "") == "recording":
                    self.notifications.hide()
            except Exception:
                pass
            return True
        except Exception as exc:
            log_exception("Could not repair idle recording state", exc, reason=reason)
            return False

    def _force_stop_recording_state(self, reason: str) -> None:
        """Emergency stop used by tray/menu/hotkey when state is inconsistent."""
        try:
            log_warning("Force-stopping recording state", reason=reason, session_id=self.recording_session_id)
            self.log_state("recording", "force_stop_requested", reason=reason)
            try:
                self.recorder.stop_discard()
            except Exception:
                try:
                    self.recorder.stop_stream_keep_frames()
                except Exception:
                    pass
                try:
                    self.recorder.discard_frames()
                except Exception:
                    pass
            try:
                self._stop_realtime_streaming(wait=False)
            except Exception:
                pass
            self.finalizing_recording = False
            self.recording_start_in_progress = False
            self.pending_hotkey_start_requested = False
            self.pending_hotkey_start_target = None
            self.streaming_thread = None
            self.last_wav_path = None
            self.record_started_at = None
            if self.timer_job:
                try:
                    self.root.after_cancel(self.timer_job)
                except Exception:
                    pass
                self.timer_job = None
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            try:
                self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            except Exception:
                pass
            try:
                if getattr(self.notifications, "_last_kind", "") == "recording":
                    self.notifications.hide()
            except Exception:
                pass
        except Exception as exc:
            log_exception("Emergency force stop failed", exc, reason=reason)

    def toggle_recording(self, origin: str = "main", captured_target: Optional[PasteTarget] = None) -> None:
        self.log_state("recording", "toggle_requested", origin=origin, captured_target=self._hotkey_target_snapshot(captured_target))
        if origin == "hotkey":
            self.log_hotkey_trace(
                "toggle_requested",
                origin=origin,
                captured_target=self._hotkey_target_snapshot(captured_target),
            )

        # If no microphone stream is active, stale finalizing/busy UI must not
        # block the next start from F9 or from the tray menu.
        self._repair_idle_recording_state(f"before_toggle:{origin}")

        if self.finalizing_recording:
            self.log_state("recording", "finalizing_force_released_before_toggle", origin=origin)
            if origin == "hotkey":
                self.log_hotkey_trace(
                    "finalizing_force_released_before_toggle",
                    captured_target=self._hotkey_target_snapshot(captured_target),
                )
            self._force_stop_recording_state(f"finalizing_before_toggle:{origin}")

        if self.status_var.get() in {"Распознаю...", "Обрабатываю...", "Обрабатываю последний фрагмент..."}:
            if not self.recorder.is_recording:
                self._repair_idle_recording_state(f"busy_status_before_toggle:{origin}")
            if self.status_var.get() in {"Распознаю...", "Обрабатываю...", "Обрабатываю последний фрагмент..."}:
                self.log_state("recording", "toggle_ignored_busy_status", origin=origin)
                return
        if self.recorder.is_recording:
            self.log_state("recording", "toggle_to_stop", origin=origin)
            self.stop_recording(origin=origin)
        else:
            self.log_state("recording", "toggle_to_start", origin=origin)
            self.start_recording(origin=origin, captured_target=captured_target)

    def start_recording(self, origin: str = "main", captured_target: Optional[PasteTarget] = None) -> None:
        if origin == "hotkey":
            self.log_hotkey_trace(
                "start_recording_called",
                origin=origin,
                captured_target=self._hotkey_target_snapshot(captured_target),
            )
        if self.recording_start_in_progress:
            log_info("Recording start ignored because another start is already in progress", origin=origin)
            self.log_state("recording", "start_ignored_already_in_progress", origin=origin)
            return
        self.recording_start_in_progress = True
        started = False
        try:
            self.log_state("recording", "start_begin", origin=origin)
            self._apply_selected_microphone()
            if not self.microphone_options:
                raise RuntimeError("Микрофон не выбран: список микрофонов пустой")
            runtime_settings = self._runtime_settings_snapshot()
            self.recording_session_id += 1
            session_id = self.recording_session_id
            log_info(
                "Recording start requested",
                origin=origin,
                session_id=session_id,
                microphone=self.microphone_var.get(),
                settings=asdict(runtime_settings),
            )
            self.processing_origin = origin
            self.last_result_ready = False
            if origin == "hotkey":
                self.paste_target = captured_target or get_paste_target()
            self.clear_texts(keep_status=True)
            self.recorder.start()
            started = True
            self.record_started_at = time.time()
            self.status_var.set("Идёт запись...")
            self.record_btn.config(text="■ Остановить запись")
            self.hotkey_ignore_until = max(
                self.hotkey_ignore_until,
                time.monotonic() + HOTKEY_START_GUARD_SECONDS,
            )
            # Recreate the toast on every new recording start. Reusing a hidden
            # Toplevel after previous success/stop notifications can make the
            # user think recording did not start, even though audio and realtime
            # insertion are already working.
            self._show_recording_notification(force_recreate=True)
            self.log_state("recording", "start_success", origin=origin, session_id=session_id)
            if origin == "hotkey":
                self.log_hotkey_trace(
                    "start_recording_success",
                    origin=origin,
                    session_id=session_id,
                    record_started_at=self.record_started_at,
                    microphone=self.microphone_var.get(),
                )
            self._start_realtime_streaming_if_enabled(origin, session_id, runtime_settings)
            self._update_timer()
        except Exception as exc:
            log_exception("Could not start recording", exc, origin=origin)
            self.log_state("recording", "start_failed", origin=origin, error=str(exc))
            if origin == "hotkey":
                self.log_hotkey_trace("start_recording_failed", origin=origin, error=str(exc))
            self.notify("⚠ Не удалось начать запись", kind="error", duration_ms=3000)
            self._show_error("Не удалось начать запись", exc)
        finally:
            self.recording_start_in_progress = False
            if not started:
                self.hotkey_ignore_until = max(
                    self.hotkey_ignore_until,
                    time.monotonic() + HOTKEY_DEBOUNCE_SECONDS,
                )

    def stop_recording(self, origin: str = "main") -> None:
        if origin == "hotkey":
            self.log_hotkey_trace("stop_recording_called", origin=origin, session_id=self.recording_session_id)
        """Stop recording without any final paste.

        This build is realtime-only: confirmed chunks are inserted while recording.
        When the user presses the hotkey again, recording stops and the app does
        not transcribe/paste the whole final recording, so there are no duplicate
        blocks after dictation.
        """
        try:
            self.log_state("recording", "stop_begin", origin=origin, session_id=self.recording_session_id)
            self.hotkey_ignore_until = max(
                self.hotkey_ignore_until,
                time.monotonic() + HOTKEY_DEBOUNCE_SECONDS,
            )
            self.processing_origin = origin
            log_info("Recording stop requested", origin=origin, session_id=self.recording_session_id)
            self.recorder.stop_stream_keep_frames()
            stopped_session_id = self.recording_session_id
            finishing_thread = self.streaming_thread
            self._stop_realtime_streaming(wait=False)
            self.last_wav_path = None
            if self.timer_job:
                self.root.after_cancel(self.timer_job)
                self.timer_job = None

            # Do not block the hotkey while the old streaming worker exits.
            # The app is realtime-only: the important text is committed while
            # recording. Waiting here for a final chunk made F9 appear broken
            # after the first stop because the UI stayed in finalizing state.
            self.finalizing_recording = False
            self.pending_hotkey_start_requested = False
            self.pending_hotkey_start_target = None
            self.streaming_thread = None
            self.recorder.discard_frames()
            self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            self.log_state(
                "recording",
                "stop_released_immediately",
                origin=origin,
                session_id=stopped_session_id,
                finishing_thread_alive=bool(finishing_thread is not None and finishing_thread.is_alive()),
                final_chunk_on_stop=STREAM_FINAL_CHUNK_ON_STOP,
            )
            if origin == "hotkey":
                self.log_hotkey_trace(
                    "stop_released_immediately",
                    origin=origin,
                    session_id=stopped_session_id,
                    finishing_thread_alive=bool(finishing_thread is not None and finishing_thread.is_alive()),
                    final_chunk_on_stop=STREAM_FINAL_CHUNK_ON_STOP,
                )
            self.notify(
                "⏹ Запись остановлена\nГорячая клавиша снова доступна сразу",
                kind="success",
                duration_ms=1200,
            )
        except Exception as exc:
            log_exception("Could not stop recording", exc, origin=origin, session_id=self.recording_session_id)
            self.log_state("recording", "stop_failed", origin=origin, session_id=self.recording_session_id, error=str(exc))
            self._force_stop_recording_state(f"stop_failed:{origin}")
            self.notify("⚠ Не удалось остановить запись, состояние сброшено", kind="error", duration_ms=3000)
            self._show_error("Не удалось остановить запись", exc)

    def _get_realtime_chunk_seconds(self) -> int:
        try:
            value = int(self.realtime_chunk_seconds_var.get())
        except Exception:
            value = 4
        return max(1, min(10, value))

    def _runtime_settings_snapshot(self) -> RuntimeSettings:
        return RuntimeSettings(
            mode=self.mode_var.get(),
            language=self.language_var.get(),
            whisper_model=(
                self.whisper_model_var.get()
                if self.whisper_model_var.get() in WHISPER_MODEL_OPTIONS
                else LOCAL_WHISPER_MODEL
            ),
            recognition_quality=(
                self.recognition_quality_var.get()
                if self.recognition_quality_var.get() in QUALITY_OPTIONS
                else "Максимальная точность"
            ),
            inference_device=(
                self.inference_device_var.get()
                if self.inference_device_var.get() in INFERENCE_DEVICE_OPTIONS
                else "auto"
            ),
            compute_type=(
                self.compute_type_var.get()
                if self.compute_type_var.get() in COMPUTE_TYPE_OPTIONS
                else "auto"
            ),
            use_vad_filter=self.use_vad_filter_var.get(),
            custom_terms=self.custom_terms_var.get(),
            deep_grammar=self.deep_grammar_var.get(),
            realtime_chunk_seconds=self._get_realtime_chunk_seconds(),
            realtime_fast_quality=self.realtime_fast_quality_var.get(),
            realtime_speed_profile=(
                self.realtime_speed_profile_var.get()
                if self.realtime_speed_profile_var.get() in STREAMING_SPEED_OPTIONS
                else "Быстрее"
            ),
        )

    def _cpu_realtime_path_expected(self, runtime_settings: RuntimeSettings) -> bool:
        missing_cuda_dlls = bool(self.transcriber._windows_missing_cuda_dlls())
        if runtime_settings.inference_device == "cpu":
            return True
        if runtime_settings.inference_device == "cuda":
            return missing_cuda_dlls
        return missing_cuda_dlls

    def _streaming_model_name(self, runtime_settings: RuntimeSettings) -> str:
        model_name = runtime_settings.whisper_model
        if not self._cpu_realtime_path_expected(runtime_settings):
            return model_name
        if model_name in {"medium", "large-v3"}:
            return "small"
        if model_name == "small" and runtime_settings.realtime_speed_profile == "Быстрее":
            return "base"
        return model_name

    def _schedule_warmup_transcriber(self, delay_ms: int = 650) -> None:
        try:
            if self.transcriber_warmup_job is not None:
                self.root.after_cancel(self.transcriber_warmup_job)
        except Exception:
            pass
        self.transcriber_warmup_job = self.root.after(delay_ms, self._run_scheduled_warmup_transcriber)

    def _run_scheduled_warmup_transcriber(self) -> None:
        self.transcriber_warmup_job = None
        self._warmup_transcriber_async()

    def _transcriber_settings_changed(self, _event: object = None) -> None:
        self._save_settings()
        self._schedule_warmup_transcriber(650)

    def _warmup_transcriber_async(self) -> None:
        try:
            runtime_settings = self._runtime_settings_snapshot()
            streaming_model_name = self._streaming_model_name(runtime_settings)
            warm_device = "cpu" if self._cpu_realtime_path_expected(runtime_settings) else runtime_settings.inference_device
            warm_compute = runtime_settings.compute_type
            if warm_device == "cpu" and warm_compute not in {"int8", "float32"}:
                warm_compute = "int8"
        except Exception as exc:
            log_exception("Could not build transcriber warmup settings", exc)
            return

        def worker() -> None:
            try:
                log_info(
                    "Transcriber warmup started",
                    selected_model=runtime_settings.whisper_model,
                    streaming_model=streaming_model_name,
                    device=warm_device,
                    compute_type=warm_compute,
                )
                self.transcriber._load_model(streaming_model_name, device=warm_device, compute_type=warm_compute)
                log_info(
                    "Transcriber warmup finished",
                    streaming_model=streaming_model_name,
                    backend=self.transcriber.active_backend_label,
                )
            except BaseException as exc:
                log_exception(
                    "Transcriber warmup failed",
                    exc,
                    streaming_model=streaming_model_name,
                    device=warm_device,
                    compute_type=warm_compute,
                )

        threading.Thread(target=worker, name="voiceflow-transcriber-warmup", daemon=True).start()
