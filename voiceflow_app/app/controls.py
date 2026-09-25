"""Microphone selection and hotkey editor controls.

Methods keep their original implementation while the physical module boundary
makes navigation, review and future extraction safer.
"""

from __future__ import annotations

from tkinter import messagebox

from ..config import APP_NAME, HOTKEY_CAPTURE_CLEAR_KEYS, HOTKEY_MODIFIERS
from ..dependencies import get_input_devices
from ..diagnostics import log_exception, log_info, log_warning
from ..hotkey_config import (
    canonical_hotkey,
    is_valid_hotkey_non_modifier,
    normalize_hotkey,
    pretty_hotkey,
    tk_event_to_hotkey_part,
)


class ControlsMixin:
    def refresh_microphones(self, show_message: bool = False) -> None:
        try:
            self.microphone_options = get_input_devices()
            labels = [label for _device_id, label in self.microphone_options]
            self.microphone_box["values"] = labels
            log_info("Microphone list refreshed", count=len(labels), show_message=show_message)

            if not labels:
                self.microphone_var.set("Микрофоны не найдены")
                self.recorder.device = None
                log_warning("No input microphones found")
                if show_message:
                    messagebox.showwarning(APP_NAME, "Микрофоны не найдены. Проверь подключение и разрешения Windows.")
                return

            current = self.microphone_var.get()
            if current not in labels:
                default_label = next((label for label in labels if "по умолчанию" in label), labels[0])
                self.microphone_var.set(default_label)

            self._apply_selected_microphone()

            if show_message:
                messagebox.showinfo(APP_NAME, "Список микрофонов обновлён.")
        except Exception as exc:
            log_exception("Could not refresh microphone list", exc)
            self._show_error("Не удалось получить список микрофонов", exc)

    def _cancel_microphone_auto_apply(self) -> None:
        if self.microphone_apply_job is None:
            return
        try:
            self.root.after_cancel(self.microphone_apply_job)
        except Exception:
            pass
        self.microphone_apply_job = None

    def _schedule_microphone_auto_apply(self, *_args: object, immediate: bool = False) -> None:
        if self._suspend_microphone_auto_apply:
            return
        self._cancel_microphone_auto_apply()
        delay_ms = 0 if immediate else 80
        self.microphone_apply_job = self.root.after(delay_ms, self._auto_apply_selected_microphone)

    def _auto_apply_selected_microphone(self) -> None:
        self.microphone_apply_job = None
        self._apply_selected_microphone(show_status=True)

    def _apply_selected_microphone(self, show_status: bool = False) -> None:
        selected_label = self.microphone_var.get()
        for device_id, label in self.microphone_options:
            if label == selected_label:
                self.recorder.device = device_id
                self.settings.microphone_label = selected_label
                log_info("Microphone selected", device_id=device_id, label=selected_label)
                self._save_settings()
                if show_status:
                    short_label = selected_label[:78] + "..." if len(selected_label) > 78 else selected_label
                    self.status_var.set(f"Микрофон применён: {short_label}")
                return
        self.recorder.device = None
        self.settings.microphone_label = ""
        log_warning("Selected microphone label not found; using default device", selected_label=selected_label)
        self._save_settings()
        if show_status:
            self.status_var.set("Микрофон не найден, используется устройство по умолчанию")

    def _cancel_hotkey_entry_apply(self) -> None:
        if self.hotkey_entry_apply_job is None:
            return
        try:
            self.root.after_cancel(self.hotkey_entry_apply_job)
        except Exception:
            pass
        self.hotkey_entry_apply_job = None

    def _set_hotkey_display_without_auto_apply(self, hotkey: str) -> None:
        self._suspend_hotkey_auto_apply = True
        try:
            self.hotkey_var.set(hotkey)
            self.pretty_hotkey_var.set(pretty_hotkey(hotkey) if hotkey else "не выбрана")
        finally:
            self._suspend_hotkey_auto_apply = False

    def _schedule_hotkey_auto_apply(self, *_args: object) -> None:
        if self._suspend_hotkey_auto_apply:
            return
        self.pretty_hotkey_var.set(pretty_hotkey(self.hotkey_var.get()) if self.hotkey_var.get().strip() else "не выбрана")
        if self.hotkey_entry_capture_active and self.hotkey_entry_pressed:
            return
        self._cancel_hotkey_entry_apply()
        self.hotkey_entry_apply_job = self.root.after(450, self._auto_apply_hotkey_from_var)

    def _auto_apply_hotkey_from_var(self) -> None:
        self.hotkey_entry_apply_job = None
        hotkey = normalize_hotkey(self.hotkey_var.get())
        if self.hotkey_handle is not None and hotkey == normalize_hotkey(self.settings.hotkey):
            self._set_hotkey_display_without_auto_apply(hotkey)
            self.status_var.set(f"Горячая клавиша уже применена: {pretty_hotkey(hotkey)}")
            return
        self.apply_hotkey_from_ui(show_message=False)

    def _begin_hotkey_entry_capture(self, _event: object = None) -> None:
        self.hotkey_entry_capture_active = True
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        self.hotkey_entry_previous_value = normalize_hotkey(self.hotkey_var.get())
        self.hotkey_entry_cleared_by_user = False
        self._cancel_hotkey_entry_apply()
        self._set_hotkey_display_without_auto_apply("")
        self.status_var.set("Нажми новое сочетание клавиш")

    def _end_hotkey_entry_capture(self, _event: object = None) -> None:
        if not self.hotkey_entry_capture_active:
            return
        self.hotkey_entry_capture_active = False
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        if not self.hotkey_var.get().strip() and not self.hotkey_entry_cleared_by_user:
            self._set_hotkey_display_without_auto_apply(self.hotkey_entry_previous_value)
            self.status_var.set(f"Горячая клавиша оставлена: {pretty_hotkey(self.hotkey_entry_previous_value)}")
            return
        self._schedule_hotkey_auto_apply()

    def _capture_hotkey_entry_keypress(self, event: object) -> str:
        self._cancel_hotkey_entry_apply()
        part = tk_event_to_hotkey_part(event)
        if part is None:
            self.status_var.set("Эта клавиша не подходит для горячей клавиши")
            return "break"
        if part in HOTKEY_CAPTURE_CLEAR_KEYS:
            self.clear_hotkey_field()
            return "break"

        if not self.hotkey_entry_pressed:
            self.hotkey_entry_pressed_order = []
        if part not in self.hotkey_entry_pressed:
            self.hotkey_entry_pressed.add(part)
            if part in HOTKEY_MODIFIERS:
                self.hotkey_entry_pressed_order.append(part)
            elif is_valid_hotkey_non_modifier(part):
                self.hotkey_entry_pressed_order.append(part)
            else:
                self.status_var.set("Используй латинскую букву, цифру, F-клавишу или служебную клавишу")
                return "break"

        hotkey = canonical_hotkey(self.hotkey_entry_pressed_order)
        self.hotkey_var.set(hotkey)
        self.pretty_hotkey_var.set(pretty_hotkey(hotkey))
        self.status_var.set(f"Выбрано сочетание: {pretty_hotkey(hotkey)}")
        return "break"

    def _capture_hotkey_entry_keyrelease(self, event: object) -> str:
        part = tk_event_to_hotkey_part(event)
        if part is not None:
            self.hotkey_entry_pressed.discard(part)

        if not self.hotkey_entry_pressed:
            raw_hotkey = self.hotkey_var.get().strip()
            if not raw_hotkey:
                return "break"
            hotkey = normalize_hotkey(raw_hotkey)
            self.hotkey_entry_pressed_order = []
            self._cancel_hotkey_entry_apply()
            self.hotkey_entry_apply_job = self.root.after(
                220,
                lambda hotkey=hotkey: self._apply_hotkey_from_entry_capture(hotkey),
            )
        return "break"

    def _finish_hotkey_entry_capture_mode(self, move_focus: bool = True) -> None:
        self.hotkey_entry_capture_active = False
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        self.hotkey_entry_cleared_by_user = False
        self.hotkey_entry_previous_value = normalize_hotkey(self.hotkey_var.get())
        if move_focus:
            try:
                self.root.focus_set()
            except Exception:
                pass

    def clear_hotkey_field(self) -> None:
        self._cancel_hotkey_entry_apply()
        self.hotkey_entry_pressed.clear()
        self.hotkey_entry_pressed_order = []
        self.hotkey_entry_cleared_by_user = True
        self._set_hotkey_display_without_auto_apply("")
        self.status_var.set("Горячая клавиша очищена. Нажми новое сочетание.")
        try:
            self.hotkey_entry.focus_set()
        except Exception:
            pass

    def _apply_hotkey_from_entry_capture(self, hotkey: str) -> None:
        self.hotkey_entry_apply_job = None
        self._set_hotkey_display_without_auto_apply(normalize_hotkey(hotkey))
        self.apply_hotkey_from_ui(show_message=False)
        self._finish_hotkey_entry_capture_mode(move_focus=True)
        log_info("Hotkey captured from entry", hotkey=self.hotkey_var.get())
