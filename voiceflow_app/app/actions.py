"""Result actions, insertion, persistence and application shutdown.

Methods keep their original implementation while the physical module boundary
makes navigation, review and future extraction safer.
"""

from __future__ import annotations

import logging
import platform
import sys
import time
import traceback
import tkinter as tk
from tkinter import messagebox

from ..config import (
    APP_NAME,
    COMPUTE_TYPE_OPTIONS,
    INFERENCE_DEVICE_OPTIONS,
    IS_WINDOWS,
    LOCAL_WHISPER_MODEL,
    QUALITY_OPTIONS,
    STREAMING_MODE_OPTIONS,
    STREAMING_SPEED_OPTIONS,
    WHISPER_MODEL_OPTIONS,
)
from ..dependencies import keyboard, pyperclip, pystray
from ..diagnostics import (
    log_category,
    log_event,
    log_exception,
    log_info,
    log_warning,
)
from ..hotkey_config import normalize_hotkey, pretty_hotkey
from ..settings import SettingsStore
from ..windows import (
    get_paste_target,
    is_windows_startup_enabled,
    send_ctrl_v_native,
    set_windows_startup_enabled,
)


class ActionsMixin:
    def get_raw_text(self) -> str:
        return self.raw_text.get("1.0", tk.END).strip()

    def get_clean_text(self) -> str:
        return self.clean_text.get("1.0", tk.END).strip()

    def get_text_for_insertion(self) -> str:
        if self.insert_edited_text_var.get():
            return self.get_clean_text()
        return self.get_raw_text()

    def copy_result(self, show_messages: bool = True, edited: bool = True) -> bool:
        text = self.get_clean_text() if edited else self.get_raw_text()
        if not text:
            log_warning("Copy requested with empty text", edited=edited)
            if show_messages:
                messagebox.showinfo(APP_NAME, "Нет текста для копирования.")
            return False
        if pyperclip is None:
            log_warning("Copy failed because pyperclip is not installed", edited=edited)
            if show_messages:
                messagebox.showerror(APP_NAME, "Не установлен pyperclip. Выполни: pip install pyperclip")
            return False
        pyperclip.copy(text)
        self.status_var.set("Скопировано")
        log_info("Text copied to clipboard", edited=edited, chars=len(text))
        return True

    def paste_text_to_current_target(self, text: str, show_messages: bool = True) -> bool:
        """Copy provided text and paste it into the currently focused field/window.

        Important realtime behavior: do not restore the window that was active
        when dictation started. The user can move the caret to another app or
        another input field while recording, and the next chunk will be inserted
        exactly there.
        """
        text = str(text or "")
        # Whitespace-only text is valid for voice commands: "новая строка",
        # "новый абзац", "пробел" and "табуляция". Only a truly empty
        # string should be rejected.
        if text == "":
            log_warning("Paste requested with empty text")
            if show_messages:
                messagebox.showinfo(APP_NAME, "Нет текста для вставки.")
            return False
        if pyperclip is None:
            log_warning("Paste failed because pyperclip is not installed")
            if show_messages:
                messagebox.showerror(
                    APP_NAME,
                    "Для вставки нужен pyperclip. Выполни: pip install pyperclip",
                )
            return False

        try:
            # Copy first, then send Ctrl+V to the currently active field.
            # No SetForegroundWindow/restore call is used here on purpose.
            current_target = get_paste_target()
            pyperclip.copy(text)
            time.sleep(0.025)
            if not send_ctrl_v_native():
                raise RuntimeError("Не удалось отправить Ctrl+V")
            self.status_var.set("Вставлено")
            log_info(
                "Text pasted to current target",
                chars=len(text),
                current_foreground_hwnd=current_target.foreground_hwnd,
                current_focus_hwnd=current_target.focus_hwnd,
            )
            log_category(
                "insertion",
                "paste_success",
                chars=len(text),
                current_foreground_hwnd=current_target.foreground_hwnd,
                current_focus_hwnd=current_target.focus_hwnd,
            )
            return True
        except Exception as exc:
            log_exception("Could not paste text to current target", exc, chars=len(text))
            log_category("insertion", "paste_failed", chars=len(text), error=str(exc))
            # Keep text in clipboard for manual Ctrl+V.
            if show_messages:
                self._show_error("Не удалось вставить текст", exc)
            else:
                print("Не удалось вставить текст", exc, file=sys.stderr)
            return False

    def paste_text_to_saved_target(self, text: str, show_messages: bool = True) -> bool:
        """Compatibility wrapper: realtime insertion now follows the current cursor."""
        return self.paste_text_to_current_target(text, show_messages=show_messages)

    def paste_result_to_saved_target(self, show_messages: bool = True) -> bool:
        """Copy chosen text and paste it into the currently focused field/window."""
        return self.paste_text_to_current_target(self.get_text_for_insertion(), show_messages=show_messages)

    def paste_result(self) -> None:
        ok = self.copy_result(show_messages=True, edited=self.insert_edited_text_var.get())
        if ok:
            self.notify("✅ Текст скопирован\nПерейди в нужное поле и нажми Ctrl+V", kind="success", duration_ms=3200)

    def clear_texts(self, keep_status: bool = False) -> None:
        self.raw_text.delete("1.0", tk.END)
        self.clean_text.delete("1.0", tk.END)
        self.timer_var.set("00:00")
        self.last_result_ready = False
        if not keep_status:
            self.status_var.set("Готово")

    def hide_main_window(self, show_notification: bool = True) -> None:
        try:
            self.hotkey_entry_capture_active = False
            self.hotkey_entry_pressed.clear()
            self.hotkey_entry_pressed_order = []
            if self.root.winfo_viewable():
                self._save_settings()
            if self.tray_started:
                self.root.withdraw()
                log_info("Main window hidden to tray/background")
                if not show_notification:
                    print(
                        f"{APP_NAME} запущен в трее. Горячая клавиша: {pretty_hotkey(self.hotkey_var.get())}. "
                        "Чтобы открыть окно, найди значок VoiceFlow рядом с часами или запусти с --show.",
                        file=sys.stderr,
                    )
            else:
                self.root.iconify()
                log_info("Main window minimized because tray icon is unavailable")
                if not show_notification:
                    print(
                        f"{APP_NAME} запущен, но значок трея недоступен. "
                        "Установи: py -m pip install pystray pillow",
                        file=sys.stderr,
                    )
        except Exception as exc:
            log_exception("Could not hide main window", exc)
        if show_notification and self.show_notifications_var.get():
            suffix = "\nЗначок трея недоступен: установи pystray pillow" if not self.tray_started else ""
            self.notify(
                f"VoiceFlow работает в фоне\nГорячая клавиша: {pretty_hotkey(self.hotkey_var.get())}{suffix}",
                kind="idle",
                duration_ms=2600,
            )

    def show_main_window(self) -> None:
        self.root.deiconify()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        self.root.focus_force()
        log_info("Main window shown")

    def _save_settings(self) -> None:
        self.settings.mode = self.mode_var.get()
        self.settings.language = self.language_var.get()
        self.settings.privacy_mode = self.privacy_var.get()
        self.settings.microphone_label = self.microphone_var.get()
        self.settings.hotkey = normalize_hotkey(self.hotkey_var.get())
        self.settings.auto_paste_after_hotkey = self.auto_paste_hotkey_var.get()
        self.settings.insert_edited_text = self.insert_edited_text_var.get()
        self.settings.show_notifications = self.show_notifications_var.get()
        self.settings.launch_at_startup = self.launch_at_startup_var.get()
        self.settings.whisper_model = self.whisper_model_var.get() if self.whisper_model_var.get() in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL
        self.settings.recognition_quality = self.recognition_quality_var.get() if self.recognition_quality_var.get() in QUALITY_OPTIONS else "Максимальная точность"
        self.settings.inference_device = self.inference_device_var.get() if self.inference_device_var.get() in INFERENCE_DEVICE_OPTIONS else "auto"
        self.settings.compute_type = self.compute_type_var.get() if self.compute_type_var.get() in COMPUTE_TYPE_OPTIONS else "auto"
        self.settings.use_vad_filter = self.use_vad_filter_var.get()
        self.settings.custom_terms = self.custom_terms_var.get()
        self.settings.deep_grammar = self.deep_grammar_var.get()
        self.settings.realtime_streaming_mode = (
            self.realtime_streaming_mode_var.get()
            if self.realtime_streaming_mode_var.get() in STREAMING_MODE_OPTIONS
            else "Вставлять фрагментами"
        )
        try:
            self.settings.realtime_chunk_seconds = int(self.realtime_chunk_seconds_var.get())
        except Exception:
            self.settings.realtime_chunk_seconds = 4
        self.settings.realtime_fast_quality = self.realtime_fast_quality_var.get()
        self.settings.realtime_speed_profile = self.realtime_speed_profile_var.get() if self.realtime_speed_profile_var.get() in STREAMING_SPEED_OPTIONS else "Быстрее"
        try:
            notification_position = self.notifications.manual_position
            if notification_position is None:
                self.settings.notification_x = None
                self.settings.notification_y = None
            else:
                self.settings.notification_x = int(notification_position[0])
                self.settings.notification_y = int(notification_position[1])
        except Exception:
            pass
        try:
            if self.root.winfo_exists():
                self.settings.window_geometry = self.root.geometry()
        except Exception:
            pass
        SettingsStore.save(self.settings)

    def apply_startup_setting(self) -> None:
        enabled = self.launch_at_startup_var.get()
        if not IS_WINDOWS:
            self.launch_at_startup_var.set(False)
            log_warning("Startup setting requested on non-Windows platform")
            messagebox.showwarning(APP_NAME, "Автозапуск доступен только на Windows.")
            return
        try:
            set_windows_startup_enabled(enabled)
            self.settings.launch_at_startup = enabled
            self._save_settings()
            log_info("Startup setting changed", enabled=enabled)
            if enabled:
                self.notify("✅ Автозапуск включён\nVoiceFlow будет запускаться вместе с Windows 11", kind="success", duration_ms=3200)
            else:
                self.notify("Автозапуск выключен", kind="idle", duration_ms=2200)
        except Exception as exc:
            log_exception("Could not change startup setting", exc, enabled=enabled)
            self.launch_at_startup_var.set(is_windows_startup_enabled())
            self._show_error("Не удалось изменить автозапуск", exc)

    def _show_error(self, title: str, exc: object) -> None:
        details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if isinstance(exc, BaseException):
            log_exception("UI error shown", exc, title=title)
        else:
            log_event(logging.ERROR, "UI error shown", title=title, details=details)
        messagebox.showerror(APP_NAME, f"{title}:\n\n{details}")
        print(title, details, file=sys.stderr)

    def on_close(self) -> None:
        if not self.exit_requested:
            log_info("Window close requested; hiding instead of exiting")
            self.hide_main_window(show_notification=True)
            return
        log_info("Application close requested")
        try:
            self.log_hotkey_trace("application_close_requested")
        except Exception:
            pass
        self._save_settings()
        try:
            if self.transcriber_warmup_job is not None:
                self.root.after_cancel(self.transcriber_warmup_job)
                self.transcriber_warmup_job = None
        except Exception:
            pass
        try:
            self._stop_windows_hotkey_polling()
        except Exception:
            pass
        try:
            if keyboard is not None and self.hotkey_handle is not None:
                keyboard.remove_hotkey(self.hotkey_handle)
        except Exception:
            pass
        try:
            self._stop_realtime_streaming()
        except Exception:
            pass
        try:
            self.notifications.destroy()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def exit_application(self) -> None:
        self.exit_requested = True
        self.on_close()
