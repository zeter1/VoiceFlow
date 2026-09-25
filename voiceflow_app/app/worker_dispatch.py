"""Main-thread worker queue dispatch and application event routing.

Worker producers use typed queue contracts. This mixin owns decoding, stale
session gates and translation into Tk/application actions.
"""

from __future__ import annotations

import queue

from ..diagnostics import log_exception, log_info
from ..windows import PasteTarget, get_paste_target
from ..worker_messages import (
    ExternalTogglePayload,
    HotkeyPressedPayload,
    StreamResultDisposition,
    StreamResultPayload,
    StreamWarningPayload,
    classify_stream_result,
    coerce_worker_message,
    is_current_session,
)


class WorkerDispatchMixin:
    def _handle_worker_message(self, msg_type: str, payload: object) -> None:
        if msg_type == "global_hotkey_pressed":
            try:
                if isinstance(payload, HotkeyPressedPayload):
                    hotkey, target, generation = payload.hotkey, payload.target, payload.generation
                else:
                    hotkey, target, generation = payload  # type: ignore[misc]
            except Exception:
                hotkey, target, generation = self.hotkey_var.get(), get_paste_target(), None
            self.log_hotkey_trace(
                "global_hotkey_dequeued_on_main_thread",
                hotkey=hotkey,
                generation=generation,
                target=self._hotkey_target_snapshot(target),
            )
            self._handle_global_hotkey(str(hotkey), target if isinstance(target, PasteTarget) else None)
            return

        if msg_type == "external_toggle_recording":
            try:
                if isinstance(payload, ExternalTogglePayload):
                    source, target = payload.source, payload.target
                else:
                    source, target = payload  # type: ignore[misc]
            except Exception:
                source, target = "unknown", get_paste_target()
            self.log_state(
                "recording",
                "external_toggle_dequeued",
                source=source,
                target=self._hotkey_target_snapshot(target if isinstance(target, PasteTarget) else None),
            )
            self.log_hotkey_trace(
                "external_toggle_dequeued",
                source=source,
                target=self._hotkey_target_snapshot(target if isinstance(target, PasteTarget) else None),
            )
            self._repair_idle_recording_state(f"external_toggle:{source}")
            self.toggle_recording("hotkey", target if isinstance(target, PasteTarget) else None)
            return

        if msg_type == "external_show_window":
            log_info("External show-window request", source=payload)
            self.show_main_window()
            return

        if msg_type == "external_hide_window":
            log_info("External hide-window request", source=payload)
            self.hide_main_window(show_notification=True)
            return

        if msg_type == "external_exit":
            log_info("External exit request", source=payload)
            self.exit_application()
            return

        if msg_type == "stream_result":
            commit_meta: dict[str, object] = {}
            try:
                if isinstance(payload, StreamResultPayload):
                    session_id = payload.session_id
                    raw = payload.raw
                    cleaned = payload.cleaned
                    origin = payload.origin
                    stream_mode = payload.stream_mode
                    is_final = payload.is_final
                    commit_meta = payload.commit_meta
                elif isinstance(payload, (tuple, list)) and len(payload) >= 7:
                    session_id, raw, cleaned, origin, stream_mode, is_final, commit_meta = payload[:7]  # type: ignore[misc]
                else:
                    session_id, raw, cleaned, origin, stream_mode, is_final = payload  # type: ignore[misc]
            except Exception:
                session_id, raw, cleaned, origin, stream_mode, is_final = payload  # type: ignore[misc]
                commit_meta = {}

            disposition = classify_stream_result(
                message_session_id=int(session_id),
                current_session_id=self.recording_session_id,
                recorder_active=bool(self.recorder.is_recording),
                finalizing=bool(self.finalizing_recording),
            )
            if disposition is StreamResultDisposition.STALE_SESSION:
                return
            if disposition is StreamResultDisposition.AFTER_STOP:
                log_info(
                    "Late realtime stream result ignored after immediate stop",
                    session_id=session_id,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                    commit_meta=commit_meta if isinstance(commit_meta, dict) else {},
                )
                self.log_state(
                    "streaming",
                    "late_stream_result_ignored_after_stop",
                    session_id=session_id,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                )
                if origin == "hotkey":
                    self.log_hotkey_trace(
                        "late_stream_result_ignored_after_stop",
                        session_id=session_id,
                        stream_mode=stream_mode,
                        is_final=is_final,
                    )
                return
            original_raw = raw
            original_cleaned = cleaned
            plan = self.realtime_text_pipeline.plan_stream_result(
                raw,
                cleaned,
                previous_inserted_text=self.stream_inserted_text,
                origin=origin,
                stream_mode=stream_mode,
                insert_edited_text=bool(self.insert_edited_text_var.get()),
                commit_meta=commit_meta if isinstance(commit_meta, dict) else {},
            )
            if plan.raw or plan.cleaned:
                self._handle_stream_text_piece(
                    session_id=session_id,
                    raw=plan.raw,
                    cleaned=plan.cleaned,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                    insertion_plan=plan.insertion,
                    commit_meta=commit_meta if isinstance(commit_meta, dict) else {},
                )
            if plan.command and plan.execute_command:
                self._execute_voice_control_command(
                    plan.command,
                    session_id=session_id,
                    raw_text=original_raw,
                    cleaned_text=original_cleaned,
                    origin=origin,
                )
                return

        elif msg_type == "stream_warning":
        elif msg_type == "stream_warning":
            if isinstance(payload, StreamWarningPayload):
                session_id, _exc = payload.session_id, payload.error
            else:
                session_id, _exc = payload  # type: ignore[misc]
            if not is_current_session(int(session_id), self.recording_session_id):
                return
            # Non-fatal streaming failure. Most often this means CUDA was not ready;
            # app should stay open and either fallback to CPU or keep recording.
            self.status_var.set("Стриминг: предупреждение")
            self.notify(
                "⚠ Стриминг дал ошибку, программа не закрыта\nЕсли выбрана CUDA — попробуй auto или cpu, либо установи CUDA/cuDNN",
                kind="warning",
                duration_ms=4200,
            )

    def _drain_worker_queue(self) -> None:
        try:
            while True:
                message = coerce_worker_message(self.worker_queue.get_nowait())
                msg_type = message.kind.value
                payload = message.payload
                if msg_type in {
                    "global_hotkey_pressed",
                    "external_toggle_recording",
                    "external_show_window",
                    "external_hide_window",
                    "external_exit",
                }:
                    self.log_hotkey_trace("worker_queue_drained_message", message_type=msg_type, payload=str(payload)[:500])
                self._handle_worker_message(msg_type, payload)
        except queue.Empty:
            pass

    def _poll_recording_state_watchdog(self) -> None:
        try:
            self._repair_idle_recording_state("watchdog")
        except Exception as exc:
            log_exception("Recording state watchdog failed", exc)
        try:
            self.root.after(1000, self._poll_recording_state_watchdog)
        except Exception:
            pass

    def _poll_worker_queue(self) -> None:
        self._drain_worker_queue()
        self.root.after(100, self._poll_worker_queue)
