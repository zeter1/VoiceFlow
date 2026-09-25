"""Realtime speech orchestration, text shaping and insertion helpers.

The headless frame/transcription worker lives in app/realtime_worker.py.
"""

from __future__ import annotations

import re
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

from ..config import (
    STREAM_FINAL_CHUNK_ON_STOP,
    STREAM_FINISH_TIMEOUT_SECONDS,
)
from ..dependencies import pyautogui
from ..diagnostics import (
    log_category,
    log_dictation_text,
    log_exception,
    log_info,
    log_warning,
)
from ..settings import RuntimeSettings
from ..worker_messages import WorkerMessageKind, put_worker_message
from .realtime_text_pipeline import RealtimeInsertionPlan, RealtimeTextConfig
from .realtime_worker import RealtimeWorkerConfig, RealtimeWorkerEngine
from ..windows import PasteTarget, get_paste_target
from ..core.realtime_policy import build_realtime_timing


class StreamingMixin:
    def _start_realtime_streaming_if_enabled(
        self,
        origin: str,
        session_id: int,
        runtime_settings: RuntimeSettings,
    ) -> None:
        # Realtime-only behavior. Hotkey recording pastes fragments into whatever
        # input field is focused at the moment each fragment is ready. This lets
        # the user move the caret between windows while one dictation session is running.
        # Main-window recording can only preview because it has no external caret.
        mode = "Вставлять фрагментами"
        self.realtime_streaming_mode_var.set(mode)
        if origin != "hotkey":
            mode = "Только превью"

        streaming_model_name = self._streaming_model_name(runtime_settings)
        if streaming_model_name != runtime_settings.whisper_model:
            log_info(
                "Realtime model optimized for faster CPU streaming",
                selected_model=runtime_settings.whisper_model,
                streaming_model=streaming_model_name,
                inference_device=runtime_settings.inference_device,
                speed_profile=runtime_settings.realtime_speed_profile,
            )

        self.streaming_stop_event = threading.Event()
        self.stream_context_reset_event.clear()
        self.stream_last_frame_index = self.recorder.frames_count()
        self.session_controller.reset_commits()
        self.stream_preview_raw_text = ""
        self.stream_preview_clean_text = ""
        self.streaming_thread = threading.Thread(
            target=self._realtime_stream_worker,
            args=(origin, mode, self.streaming_stop_event, session_id, runtime_settings),
            daemon=True,
        )
        self.streaming_thread.start()
        log_info(
            "Realtime streaming worker started",
            origin=origin,
            mode=mode,
            session_id=session_id,
            realtime_chunk_seconds=runtime_settings.realtime_chunk_seconds,
            realtime_speed_profile=runtime_settings.realtime_speed_profile,
            streaming_model=streaming_model_name,
        )
        self.log_state(
            "streaming",
            "streaming_worker_started",
            origin=origin,
            mode=mode,
            session_id=session_id,
            realtime_chunk_seconds=runtime_settings.realtime_chunk_seconds,
            realtime_speed_profile=runtime_settings.realtime_speed_profile,
            streaming_model=streaming_model_name,
        )

    def _stop_realtime_streaming(self, wait: bool = False) -> None:
        try:
            self.streaming_stop_event.set()
        except Exception:
            pass
        if wait and self.streaming_thread is not None and self.streaming_thread.is_alive():
            self.status_var.set("Обрабатываю последний фрагмент...")
            log_info("Waiting for realtime streaming worker to finish", session_id=self.recording_session_id)
            self.streaming_thread.join()
            self.streaming_thread = None

    def _finish_streaming_after_stop_async(
        self,
        session_id: int,
        finishing_thread: Optional[threading.Thread],
    ) -> None:
        def waiter() -> None:
            finish_message_sent = False
            try:
                if finishing_thread is not None and finishing_thread.is_alive():
                    log_info("Waiting for realtime streaming worker in background", session_id=session_id)
                    finishing_thread.join(timeout=STREAM_FINISH_TIMEOUT_SECONDS)
                    if finishing_thread.is_alive():
                        log_warning(
                            "Realtime streaming worker finish timed out; releasing UI",
                            session_id=session_id,
                            timeout_seconds=STREAM_FINISH_TIMEOUT_SECONDS,
                        )
                        put_worker_message(
                            self.worker_queue,
                            WorkerMessageKind.STREAM_FINISH_TIMEOUT,
                            session_id,
                        )
                        finish_message_sent = True
                        return
            except BaseException as exc:
                log_exception("Background wait for realtime worker failed", exc, session_id=session_id)
            finally:
                if not finish_message_sent:
                    put_worker_message(
                        self.worker_queue,
                        WorkerMessageKind.STREAM_FINISHED,
                        session_id,
                    )

        threading.Thread(target=waiter, name="voiceflow-stream-finalizer", daemon=True).start()

    def _stream_quality(self, runtime_settings: RuntimeSettings) -> str:
        profile = runtime_settings.realtime_speed_profile
        if runtime_settings.realtime_fast_quality or profile == "Быстрее":
            return "Быстро"
        if profile == "Баланс":
            return "Точно"
        if self._cpu_realtime_path_expected(runtime_settings):
            return "Точно"
        return runtime_settings.recognition_quality

    def _realtime_stream_worker(
        self,
        origin: str,
        mode: str,
        stop_event: threading.Event,
        session_id: int,
        runtime_settings: RuntimeSettings,
    ) -> None:
        timing = build_realtime_timing(
            runtime_settings.realtime_speed_profile,
            runtime_settings.realtime_chunk_seconds,
            use_vad_filter=runtime_settings.use_vad_filter,
            cpu_path_expected=self._cpu_realtime_path_expected(runtime_settings),
        )
        text_config = RealtimeTextConfig(
            mode=runtime_settings.mode,
            language=runtime_settings.language,
            custom_terms=runtime_settings.custom_terms,
        )
        engine = RealtimeWorkerEngine(
            recorder=self.recorder,
            session_controller=self.session_controller,
            message_queue=self.worker_queue,
            config=RealtimeWorkerConfig(
                session_id=session_id,
                origin=origin,
                mode=mode,
                language=runtime_settings.language,
                model_name=self._streaming_model_name(runtime_settings),
                quality=self._stream_quality(runtime_settings),
                custom_terms=runtime_settings.custom_terms,
                inference_device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                speed_profile=runtime_settings.realtime_speed_profile,
                timing=timing,
                final_chunk_on_stop=STREAM_FINAL_CHUNK_ON_STOP,
            ),
            start_frame_index=self.stream_last_frame_index,
            context_reset_event=self.stream_context_reset_event,
            clean_chunk=lambda raw_text, keep_sentence_end: self.realtime_text_pipeline.clean_for_commit(
                raw_text,
                text_config,
                keep_sentence_end=keep_sentence_end,
            ),
            dedupe_chunk=self.realtime_text_pipeline.dedupe_chunk,
        )
        engine.run(stop_event)

    def _append_stream_text(self, widget: tk.Text, text: str) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", (text or "").strip())
        if not text:
            return
        current = widget.get("1.0", tk.END).strip()
        if current:
            separator = "" if text.startswith("\n") else " "
            widget.insert(tk.END, separator + text)
        else:
            widget.insert(tk.END, text)
        widget.see(tk.END)

    def _update_timer(self) -> None:
        if not self.recorder.is_recording or self.record_started_at is None:
            return
        elapsed = int(time.time() - self.record_started_at)
        minutes, seconds = divmod(elapsed, 60)
        current = f"{minutes:02d}:{seconds:02d}"
        self.timer_var.set(current)
        # Update persistent recording notification once per second without creating a new window.
        if elapsed % 1 == 0:
            self._show_recording_notification(force_recreate=False)
        self.timer_job = self.root.after(1000, self._update_timer)

    def _process_audio_worker(self, wav_path: Path, origin: str, runtime_settings: RuntimeSettings) -> None:
        try:
            raw = self.transcriber.transcribe(
                wav_path,
                runtime_settings.language,
                model_name=runtime_settings.whisper_model,
                quality=runtime_settings.recognition_quality,
                custom_terms=runtime_settings.custom_terms,
                use_vad_filter=runtime_settings.use_vad_filter,
                device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                streaming=False,
            )
            cleaned = self.cleaner.clean(
                raw,
                runtime_settings.mode,
                runtime_settings.language,
                custom_terms=runtime_settings.custom_terms,
                deep_grammar=runtime_settings.deep_grammar,
            )
            log_dictation_text(
                "final_result",
                session_id=getattr(self, "recording_session_id", None),
                origin=origin,
                mode=runtime_settings.mode,
                raw_text=raw,
                cleaned_text=cleaned,
                model=runtime_settings.whisper_model,
                language=runtime_settings.language,
                device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                deep_grammar=runtime_settings.deep_grammar,
            )
            if self.privacy_var.get():
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
            put_worker_message(
                self.worker_queue,
                WorkerMessageKind.RESULT,
                (raw, cleaned, origin),
            )
        except BaseException as exc:
            log_exception("Audio processing worker crashed", exc, wav_path=wav_path, origin=origin)
            put_worker_message(self.worker_queue, WorkerMessageKind.ERROR, exc)

    def _reset_stream_message_state(self, session_id: Optional[int], reason: str) -> None:
        self.session_controller.reset_commits()
        self.stream_context_reset_event.set()
        log_info("Realtime message state reset", session_id=session_id, reason=reason)

    def _handle_stream_text_piece(
        self,
        *,
        session_id: int,
        raw: str,
        cleaned: str,
        origin: str,
        stream_mode: str,
        is_final: bool,
        insertion_plan: Optional[RealtimeInsertionPlan] = None,
        commit_meta: Optional[dict[str, object]] = None,
    ) -> None:
        raw = (raw or "").strip()
        cleaned = (cleaned or "").strip()
        if not raw and not cleaned:
            return
        if not raw:
            raw = cleaned
        if not cleaned:
            cleaned = raw

        self._append_stream_text(self.raw_text, raw)
        self._append_stream_text(self.clean_text, cleaned)
        self.status_var.set("Готово" if is_final and not self.recorder.is_recording else "Стриминг...")

        if insertion_plan is not None:
            punctuation = insertion_plan.punctuation
            log_category(
                "streaming",
                "pause_punctuation_decision",
                chunk_preview=insertion_plan.text[:160],
                pause_seconds=round(punctuation.pause_seconds, 3),
                sentence_pause=punctuation.sentence_pause,
                whisper_sentence_end=punctuation.whisper_sentence_end,
                previous_had_sentence_end=punctuation.previous_had_sentence_end,
                forced_commit=punctuation.forced_commit,
                selected_source=insertion_plan.selected_source,
            )
            ok = self.paste_text_to_current_target(insertion_plan.paste_text, show_messages=False)
            log_dictation_text(
                "stream_insert",
                session_id=session_id,
                origin=origin,
                mode=stream_mode,
                raw_text=raw,
                cleaned_text=cleaned,
                inserted_text=insertion_plan.text,
                selected_source=insertion_plan.selected_source,
                is_final=is_final,
                paste_ok=ok,
                commit_meta=commit_meta or {},
            )
            if ok:
                self.session_controller.record_commit(insertion_plan.text)
                if self.recorder.is_recording:
                    self._show_recording_notification(force_recreate=False)
                else:
                    message = "✅ Финальный фрагмент вставлен" if is_final else "⚡ Стабильный фрагмент вставлен"
                    self.notify(message, kind="success", duration_ms=1200 if is_final else 900)
            else:
                self.notify("⚠ Фрагмент распознан, но не вставился", kind="warning", duration_ms=1600)

    def _execute_voice_control_command(
        self,
        command: dict[str, object],
        *,
        session_id: Optional[int],
        raw_text: str,
        cleaned_text: str,
        origin: str,
    ) -> bool:
        kind = str(command.get("kind", ""))
        value = command.get("value")
        label = str(command.get("label", command.get("phrase", "voice command")))
        phrase = str(command.get("phrase", ""))
        ok = False

        if kind == "text":
            ok = self.paste_text_to_current_target(str(value or ""), show_messages=False)
        else:
            current_target = get_paste_target()
            if pyautogui is not None:
                try:
                    # Voice control hotkeys/keys should also act in the current
                    # field under the cursor, not in the window where recording started.
                    time.sleep(0.025)

                    def run_action(action_kind: str, action_value: object) -> None:
                        if action_kind == "key":
                            pyautogui.press(str(action_value))
                        elif action_kind == "hotkey" and isinstance(action_value, (tuple, list)):
                            pyautogui.hotkey(*(str(part) for part in action_value))
                        else:
                            raise ValueError(f"Unsupported voice action: {action_kind}")

                    if kind in {"key", "hotkey"}:
                        run_action(kind, value)
                        ok = True
                    elif kind == "sequence" and isinstance(value, (tuple, list)):
                        for step in value:
                            if not isinstance(step, (tuple, list)) or len(step) != 2:
                                raise ValueError(f"Bad voice action step: {step!r}")
                            run_action(str(step[0]), step[1])
                            time.sleep(0.025)
                        ok = True
                    log_info(
                        "Voice control key action sent to current target",
                        command=phrase,
                        kind=kind,
                        current_foreground_hwnd=current_target.foreground_hwnd,
                        current_focus_hwnd=current_target.focus_hwnd,
                    )
                except Exception as exc:
                    log_exception("Voice control key action failed", exc, command=phrase, kind=kind, value=value)
            elif pyautogui is None:
                log_warning("Voice control key action skipped because pyautogui is not installed", command=phrase, kind=kind)

        log_info(
            "Voice control command handled",
            command=phrase,
            action=label,
            kind=kind,
            ok=ok,
            session_id=session_id,
        )
        log_dictation_text(
            "voice_command",
            session_id=session_id,
            origin=origin,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            command=phrase,
            action=label,
            ok=ok,
        )
        if ok:
            self.status_var.set(f"Команда: {label}")
            self.notify(f"🎛 Команда: {label}", kind="success", duration_ms=900)
            if command.get("reset_message_context"):
                self._reset_stream_message_state(session_id, label)
        else:
            self.notify(f"⚠ Команда распознана, но не выполнена: {label}", kind="warning", duration_ms=1800)
        return ok
