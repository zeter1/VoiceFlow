"""Global hotkey registration, polling and dispatch.

Methods keep their original implementation while the physical module boundary
makes navigation, review and future extraction safer.
"""

from __future__ import annotations

import ctypes
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from ..core.hotkey_state import (
    HotkeyEdgeState,
    advance_hotkey_edge,
    decide_hotkey_action,
)
from ..runtime import (
    APP_NAME,
    HOTKEY_DEBOUNCE_SECONDS,
    HOTKEY_START_GUARD_SECONDS,
    IS_WINDOWS,
    PasteTarget,
    WINDOWS_HOTKEY_HEARTBEAT_SECONDS,
    WINDOWS_HOTKEY_MIN_EDGE_GAP_SECONDS,
    WINDOWS_HOTKEY_POLL_INTERVAL_SECONDS,
    WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS,
    WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS,
    get_paste_target,
    hotkey_to_windows_vk_options,
    keyboard,
    log_category,
    log_exception,
    log_info,
    log_warning,
    normalize_hotkey,
    pretty_hotkey,
    repair_unreliable_modifier_only_hotkey,
    summarize_windows_hotkey_state,
)


class HotkeyMixin:
    def _setup_hotkey(self) -> None:
        requested_hotkey = self.hotkey_var.get()
        hotkey = normalize_hotkey(requested_hotkey)
        hotkey, repaired_modifier_only = repair_unreliable_modifier_only_hotkey(hotkey)
        if requested_hotkey.strip().lower() != hotkey or repaired_modifier_only:
            log_warning(
                "Hotkey normalized or repaired",
                requested=requested_hotkey,
                normalized=hotkey,
                repaired_modifier_only=repaired_modifier_only,
            )
        if repaired_modifier_only:
            self.status_var.set(
                f"Горячая клавиша {pretty_hotkey(requested_hotkey)} нестабильна. "
                f"Поставлена надёжная: {pretty_hotkey(hotkey)}"
            )

        # Remove the previous keyboard hook before configuring any new backend.
        # The last build used BOTH keyboard.add_hotkey and Windows polling. Logs
        # showed one physical F9 press was delivered twice: keyboard_hook started
        # recording, then windows_poll_detected_press immediately stopped it.
        try:
            if self.hotkey_handle is not None and keyboard is not None:
                keyboard.remove_hotkey(self.hotkey_handle)
        except Exception as exc:
            log_exception("Could not remove previous keyboard hotkey", exc, hotkey=hotkey)
        finally:
            self.hotkey_handle = None

        # On Windows use only the independent polling backend. Mixing it with
        # keyboard.add_hotkey causes duplicate callbacks from the same physical
        # key press in some foreground apps. Polling waits for key release, so
        # one F9 press = exactly one start/stop action.
        polling_started = self._restart_windows_hotkey_polling(hotkey)
        keyboard_registered = False
        trigger_on_release = False
        suppress_hotkey = False

        if IS_WINDOWS and polling_started:
            self.settings.hotkey = hotkey
            self._set_hotkey_display_without_auto_apply(hotkey)
            self._save_settings()
            log_info(
                "Global hotkey configured with Windows polling only",
                hotkey=hotkey,
                keyboard_registered=keyboard_registered,
                windows_polling_started=polling_started,
                trigger_on_release=trigger_on_release,
                suppress=suppress_hotkey,
                reason="Avoid duplicate keyboard_hook + windows_polling events for one physical press",
            )
            self.log_state(
                "hotkeys",
                "configured_windows_polling_only",
                hotkey=hotkey,
                keyboard_registered=keyboard_registered,
                windows_polling_started=polling_started,
            )
            return

        if keyboard is None:
            self.settings.hotkey = hotkey
            self._set_hotkey_display_without_auto_apply(hotkey)
            self._save_settings()
            if polling_started:
                self.status_var.set(f"Горячая клавиша установлена через Windows fallback: {pretty_hotkey(hotkey)}")
            else:
                self.status_var.set("Горячие клавиши недоступны: не установлен keyboard и Windows fallback не запустился")
            log_warning(
                "keyboard package is not installed and Windows polling is unavailable",
                hotkey=hotkey,
                polling_started=polling_started,
            )
            return

        try:
            def hotkey_callback() -> None:
                target = get_paste_target()
                log_info(
                    "Global hotkey callback received",
                    hotkey=hotkey,
                    source="keyboard_hook",
                    is_recording=self.recorder.is_recording,
                    finalizing=self.finalizing_recording,
                )
                log_category(
                    "hotkeys",
                    "callback_received",
                    hotkey=hotkey,
                    source="keyboard_hook",
                    is_recording=bool(self.recorder.is_recording),
                    finalizing=bool(self.finalizing_recording),
                    recording_start_in_progress=bool(self.recording_start_in_progress),
                    pending_hotkey_start=bool(self.pending_hotkey_start_requested),
                    hotkey_ignore_remaining=max(0.0, round(self.hotkey_ignore_until - time.monotonic(), 3)),
                )
                self.root.after(0, lambda: self._handle_global_hotkey(hotkey, target))

            try:
                self.hotkey_handle = keyboard.add_hotkey(
                    hotkey,
                    hotkey_callback,
                    suppress=False,
                    trigger_on_release=False,
                )
            except TypeError:
                self.hotkey_handle = keyboard.add_hotkey(
                    hotkey,
                    hotkey_callback,
                    suppress=False,
                )
            keyboard_registered = True
        except Exception as exc:
            self.hotkey_handle = None
            log_exception("Could not register global hotkey", exc, hotkey=hotkey)
            self.status_var.set("Горячая клавиша не применена")
            self._show_error(
                "Не удалось установить горячую клавишу. Попробуй другое сочетание или запуск от имени администратора",
                exc,
            )
            return

        self.settings.hotkey = hotkey
        self._set_hotkey_display_without_auto_apply(hotkey)
        self._save_settings()
        log_info(
            "Global hotkey configured with keyboard hook",
            hotkey=hotkey,
            keyboard_registered=keyboard_registered,
            windows_polling_started=polling_started,
            trigger_on_release=trigger_on_release,
            suppress=suppress_hotkey,
        )
        self.log_state(
            "hotkeys",
            "configured_keyboard_hook",
            hotkey=hotkey,
            keyboard_registered=keyboard_registered,
            windows_polling_started=polling_started,
        )

    def _restart_windows_hotkey_polling(self, hotkey: str) -> bool:
        """Start a Windows API backend that detects the hotkey by key state.

        Diagnostic build: writes hotkey_trace.jsonl with exact press/release
        transitions, key-state raw values, debounce decisions and app state.
        """
        self._stop_windows_hotkey_polling()
        if not IS_WINDOWS:
            return False
        vk_options = hotkey_to_windows_vk_options(hotkey)
        if not vk_options:
            log_warning("Windows hotkey polling skipped: unsupported hotkey", hotkey=hotkey)
            return False

        stop_event = threading.Event()
        self.hotkey_poll_stop_event = stop_event
        self.hotkey_poll_hotkey = hotkey
        self.hotkey_poll_generation += 1
        generation = self.hotkey_poll_generation

        def poll_worker() -> None:
            user32 = ctypes.windll.user32
            edge_state = HotkeyEdgeState()
            last_heartbeat_at = 0.0
            last_stuck_log_at = 0.0
            polls_total = 0
            polls_down = 0
            polls_up = 0
            log_info("Windows hotkey polling started", hotkey=hotkey, vk_options=vk_options, generation=generation)
            log_category("hotkeys", "windows_polling_started", hotkey=hotkey, vk_options=vk_options, generation=generation)
            self.log_hotkey_trace(
                "polling_started",
                hotkey=hotkey,
                generation=generation,
                vk_options=vk_options,
                poll_interval=WINDOWS_HOTKEY_POLL_INTERVAL_SECONDS,
                release_stable_seconds=WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS,
                min_edge_gap_seconds=WINDOWS_HOTKEY_MIN_EDGE_GAP_SECONDS,
                keyboard_module_loaded=keyboard is not None,
            )
            while not stop_event.wait(WINDOWS_HOTKEY_POLL_INTERVAL_SECONDS):
                now = time.monotonic()
                polls_total += 1
                try:
                    key_states = summarize_windows_hotkey_state(vk_options)
                    all_down = bool(key_states) and all(bool(item.get("down")) for item in key_states)
                    if all_down:
                        polls_down += 1
                    else:
                        polls_up += 1

                    if now - last_heartbeat_at >= WINDOWS_HOTKEY_HEARTBEAT_SECONDS:
                        last_heartbeat_at = now
                        self.log_hotkey_trace(
                            "polling_heartbeat",
                            hotkey=hotkey,
                            generation=generation,
                            all_down=all_down,
                            was_down=edge_state.is_down,
                            polls_total=polls_total,
                            polls_down=polls_down,
                            polls_up=polls_up,
                            key_states=key_states,
                        )

                    transition = advance_hotkey_edge(
                        edge_state,
                        all_down=all_down,
                        now=now,
                        min_edge_gap_seconds=WINDOWS_HOTKEY_MIN_EDGE_GAP_SECONDS,
                        release_stable_seconds=WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS,
                    )
                    edge_state = transition.state

                    if transition.event == "ignored_press_too_close":
                        self.log_hotkey_trace(
                            "poll_press_ignored_too_close_to_previous_edge",
                            hotkey=hotkey,
                            generation=generation,
                            edge_gap=round(transition.edge_gap or 0.0, 3),
                            key_states=key_states,
                        )
                        continue

                    if transition.event == "press":
                        self.hotkey_last_poll_press_at = now
                        target = get_paste_target()
                        log_info(
                            "Windows hotkey polling detected press",
                            hotkey=hotkey,
                            is_recording=self.recorder.is_recording,
                            finalizing=self.finalizing_recording,
                        )
                        log_category(
                            "hotkeys",
                            "windows_poll_detected_press",
                            hotkey=hotkey,
                            is_recording=bool(self.recorder.is_recording),
                            finalizing=bool(self.finalizing_recording),
                            recording_start_in_progress=bool(self.recording_start_in_progress),
                            pending_hotkey_start=bool(self.pending_hotkey_start_requested),
                            hotkey_ignore_remaining=max(0.0, round(self.hotkey_ignore_until - time.monotonic(), 3)),
                        )
                        self.log_hotkey_trace(
                            "poll_press_detected",
                            hotkey=hotkey,
                            generation=generation,
                            key_states=key_states,
                            target=self._hotkey_target_snapshot(target),
                            edge_gap=round(transition.edge_gap, 3) if transition.edge_gap is not None else None,
                            will_schedule_handle=True,
                        )
                        try:
                            self.worker_queue.put(("global_hotkey_pressed", (hotkey, target, generation)))
                            self.log_hotkey_trace(
                                "poll_press_queued_for_main_thread",
                                hotkey=hotkey,
                                generation=generation,
                                target=self._hotkey_target_snapshot(target),
                            )
                        except Exception as exc:
                            self.log_hotkey_trace(
                                "poll_press_queue_failed",
                                hotkey=hotkey,
                                generation=generation,
                                error=str(exc),
                            )
                            break

                    elif transition.event == "held":
                        held_for = transition.held_for or 0.0
                        if held_for >= WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS and now - last_stuck_log_at >= WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS:
                            last_stuck_log_at = now
                            self.log_hotkey_trace(
                                "poll_key_still_down",
                                hotkey=hotkey,
                                generation=generation,
                                held_for=round(held_for, 3),
                                key_states=key_states,
                            )

                    elif transition.event == "release_started":
                        self.log_hotkey_trace(
                            "poll_release_started",
                            hotkey=hotkey,
                            generation=generation,
                            held_for=round(transition.held_for, 3) if transition.held_for is not None else None,
                            key_states=key_states,
                        )

                    elif transition.event == "rearmed":
                        self.log_hotkey_trace(
                            "poll_release_confirmed_rearmed",
                            hotkey=hotkey,
                            generation=generation,
                            release_stable_for=round(transition.release_stable_for or 0.0, 3),
                            held_for=round(transition.held_for, 3) if transition.held_for is not None else None,
                            key_states=key_states,
                        )
                except Exception as exc:
                                self.log_hotkey_trace(
                                    "poll_press_queue_failed",
                                    hotkey=hotkey,
                                    generation=generation,
                                    error=str(exc),
                                )
                                break
                        else:
                            held_for = now - press_started_at if press_started_at else 0.0
                            if held_for >= WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS and now - last_stuck_log_at >= WINDOWS_HOTKEY_STUCK_DOWN_LOG_SECONDS:
                                last_stuck_log_at = now
                                self.log_hotkey_trace(
                                    "poll_key_still_down",
                                    hotkey=hotkey,
                                    generation=generation,
                                    held_for=round(held_for, 3),
                                    key_states=key_states,
                                )
                    else:
                        if was_down:
                            if release_started_at is None:
                                release_started_at = now
                                self.log_hotkey_trace(
                                    "poll_release_started",
                                    hotkey=hotkey,
                                    generation=generation,
                                    held_for=round(now - press_started_at, 3) if press_started_at else None,
                                    key_states=key_states,
                                )
                            elif now - release_started_at >= WINDOWS_HOTKEY_RELEASE_STABLE_SECONDS:
                                was_down = False
                                self.log_hotkey_trace(
                                    "poll_release_confirmed_rearmed",
                                    hotkey=hotkey,
                                    generation=generation,
                                    release_stable_for=round(now - release_started_at, 3),
                                    held_for=round(now - press_started_at, 3) if press_started_at else None,
                                    key_states=key_states,
                                )
                                press_started_at = None
                                release_started_at = None
                except Exception as exc:
                    log_exception("Windows hotkey polling failed", exc, hotkey=hotkey, generation=generation)
                    try:
                        self.log_hotkey_trace("polling_exception", hotkey=hotkey, generation=generation, error=str(exc))
                    except Exception:
                        pass
                    time.sleep(0.25)
            log_info("Windows hotkey polling stopped", hotkey=hotkey, generation=generation)
            log_category("hotkeys", "windows_polling_stopped", hotkey=hotkey, generation=generation)
            try:
                self.log_hotkey_trace(
                    "polling_stopped",
                    hotkey=hotkey,
                    generation=generation,
                    polls_total=polls_total,
                    polls_down=polls_down,
                    polls_up=polls_up,
                )
            except Exception:
                pass

        self.hotkey_poll_thread = threading.Thread(
            target=poll_worker,
            name="voiceflow-hotkey-poll",
            daemon=True,
        )
        self.hotkey_poll_thread.start()
        return True

    def _stop_windows_hotkey_polling(self) -> None:
        thread = self.hotkey_poll_thread
        try:
            self.log_hotkey_trace(
                "polling_stop_requested",
                hotkey=self.hotkey_poll_hotkey,
                thread_alive=bool(thread is not None and thread.is_alive()),
            )
        except Exception:
            pass
        try:
            self.hotkey_poll_stop_event.set()
        except Exception:
            pass
        self.hotkey_poll_thread = None

    def _handle_global_hotkey(self, hotkey: str, target: Optional[PasteTarget]) -> None:
        now = time.monotonic()
        self.hotkey_last_handled_at = now
        target_snapshot = self._hotkey_target_snapshot(target)
        self.log_state("hotkeys", "handle_enter", hotkey=hotkey, target=target_snapshot)
        self.log_hotkey_trace(
            "handle_enter",
            hotkey=hotkey,
            target=target_snapshot,
            ignore_until=round(self.hotkey_ignore_until, 6),
            now=round(now, 6),
            ignore_remaining=round(max(0.0, self.hotkey_ignore_until - now), 3),
        )

        # Do not let a stale "hotkey entry capture" state block real global
        # hotkeys after the user already picked F9 and returned to another app.
        if self.hotkey_entry_capture_active and self.root.winfo_viewable():
            try:
                focused_widget = self.root.focus_get()
            except Exception:
                focused_widget = None
            self.log_hotkey_trace(
                "handle_capture_mode_check",
                hotkey=hotkey,
                focused_widget=str(focused_widget),
                hotkey_entry_active=bool(self.hotkey_entry_capture_active),
            )
            if focused_widget == self.hotkey_entry:
                log_info("Global hotkey ignored while editing hotkey field")
                self.log_state("hotkeys", "ignored_editing_hotkey_field", hotkey=hotkey, target=target_snapshot)
                self.log_hotkey_trace("handle_ignored_editing_hotkey_field", hotkey=hotkey, target=target_snapshot)
                return
            self._finish_hotkey_entry_capture_mode(move_focus=False)
            self.log_state("hotkeys", "stale_hotkey_capture_released", hotkey=hotkey, target=target_snapshot)
            self.log_hotkey_trace("handle_stale_hotkey_capture_released", hotkey=hotkey, target=target_snapshot)

        hotkey_decision = decide_hotkey_action(
            now=now,
            ignore_until=self.hotkey_ignore_until,
            start_in_progress=bool(self.recording_start_in_progress),
            is_recording=bool(self.recorder.is_recording),
            finalizing=bool(self.finalizing_recording),
        )

        if hotkey_decision.action == "ignore_debounce":
            remaining = round(hotkey_decision.ignore_for_seconds, 3)
            log_info(
                "Global hotkey ignored by debounce",
                hotkey=hotkey,
                ignore_for_seconds=remaining,
                is_recording=self.recorder.is_recording,
                finalizing=self.finalizing_recording,
                starting=self.recording_start_in_progress,
            )
            self.log_state(
                "hotkeys",
                "ignored_debounce",
                hotkey=hotkey,
                ignore_for_seconds=remaining,
                target=target_snapshot,
            )
            self.log_hotkey_trace(
                "handle_ignored_debounce",
                hotkey=hotkey,
                ignore_for_seconds=remaining,
                target=target_snapshot,
                key_states=summarize_windows_hotkey_state(hotkey_to_windows_vk_options(hotkey)),
            )
            return

        if hotkey_decision.action == "ignore_start_in_progress":
            self.hotkey_ignore_until = now + HOTKEY_START_GUARD_SECONDS
            log_info("Global hotkey ignored while recording start is in progress", hotkey=hotkey)
            self.log_state("hotkeys", "ignored_start_in_progress", hotkey=hotkey, target=target_snapshot)
            self.log_hotkey_trace(
                "handle_ignored_start_in_progress",
                hotkey=hotkey,
                target=target_snapshot,
                new_ignore_until=round(self.hotkey_ignore_until, 6),
            )
            return

        decision = hotkey_decision.action
        self.hotkey_ignore_until = now + HOTKEY_DEBOUNCE_SECONDS
        self.hotkey_last_accepted_at = now
        self.log_state("hotkeys", "accepted", hotkey=hotkey, target=target_snapshot, decision=decision)
        self.log_hotkey_trace(
            "handle_accepted",
            hotkey=hotkey,
            target=target_snapshot,
            decision=decision,
            new_ignore_until=round(self.hotkey_ignore_until, 6),
            debounce_seconds=HOTKEY_DEBOUNCE_SECONDS,
        )
        self.toggle_recording("hotkey", target)
        self.toggle_recording("hotkey", target)

    def set_hotkey_preset(self, hotkey: str) -> None:
        self._set_hotkey_display_without_auto_apply(normalize_hotkey(hotkey))
        self.apply_hotkey_from_ui(show_message=False)
        self._finish_hotkey_entry_capture_mode(move_focus=True)

    def apply_hotkey_from_ui(self, show_message: bool = False) -> None:
        requested_hotkey = normalize_hotkey(self.hotkey_var.get())
        if not requested_hotkey:
            messagebox.showwarning(APP_NAME, "Введите сочетание, например ctrl+shift+space")
            return
        hotkey, repaired_modifier_only = repair_unreliable_modifier_only_hotkey(requested_hotkey)
        self._cancel_hotkey_entry_apply()
        self._set_hotkey_display_without_auto_apply(hotkey)
        self._setup_hotkey()
        if repaired_modifier_only:
            message = (
                f"Сочетание {pretty_hotkey(requested_hotkey)} состоит только из Ctrl/Shift/Alt/Win "
                f"и может срабатывать один раз, а потом переставать. "
                f"Поставлена надёжная горячая клавиша: {pretty_hotkey(hotkey)}"
            )
        else:
            message = f"Горячая клавиша установлена: {pretty_hotkey(hotkey)}"
        if show_message:
            messagebox.showinfo(APP_NAME, message)
        else:
            self.status_var.set(message)

    def capture_hotkey(self) -> None:
        if keyboard is None:
            messagebox.showerror(APP_NAME, "Не установлен keyboard. Выполни: pip install keyboard")
            return

        popup = tk.Toplevel(self.root)
        popup.title("Новое сочетание")
        popup.geometry("420x140")
        popup.resizable(False, False)
        popup.attributes("-topmost", True)
        ttk.Label(
            popup,
            text="Нажми новое сочетание клавиш\nнапример Ctrl + Shift + D",
            font=("Segoe UI", 11),
            justify="center",
        ).pack(expand=True, fill=tk.BOTH, padx=20, pady=20)

        def worker() -> None:
            try:
                captured = keyboard.read_hotkey(suppress=False)
                self.root.after(0, lambda: self._finish_hotkey_capture(popup, captured))
            except Exception as exc:
                self.root.after(0, lambda: self._finish_hotkey_capture_error(popup, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_hotkey_capture(self, popup: tk.Toplevel, captured: str) -> None:
        try:
            popup.destroy()
        except Exception:
            pass
        hotkey = normalize_hotkey(captured)
        self._set_hotkey_display_without_auto_apply(hotkey)
        self.apply_hotkey_from_ui(show_message=False)
        log_info("Hotkey captured from popup", hotkey=hotkey)

    def _finish_hotkey_capture_error(self, popup: tk.Toplevel, exc: Exception) -> None:
        try:
            popup.destroy()
        except Exception:
            pass
        self._show_error("Не удалось считать сочетание клавиш", exc)
