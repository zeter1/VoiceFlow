"""VoiceFlow main application object assembled from focused behavior mixins."""

from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from typing import Optional

from ..config import (
    APP_NAME,
    COMPUTE_TYPE_OPTIONS,
    INFERENCE_DEVICE_OPTIONS,
    IS_WINDOWS,
    LOCAL_WHISPER_MODEL,
    QUALITY_OPTIONS,
    SETTINGS_PATH,
    STREAMING_SPEED_OPTIONS,
    WHISPER_MODEL_OPTIONS,
)
from ..composition import ApplicationServices, build_default_application_services
from ..diagnostics import log_info
from ..hotkey_config import pretty_hotkey
from ..settings import SettingsStore
from ..windows import PasteTarget, is_windows_startup_enabled
from .session_controller import HeadlessSessionController
from .actions import ActionsMixin
from .controls import ControlsMixin
from .hotkeys import HotkeyMixin
from .recording import RecordingMixin
from .streaming import StreamingMixin
from .ui import UiMixin


class VoiceFlowOfflineApp(
    UiMixin,
    ControlsMixin,
    HotkeyMixin,
    RecordingMixin,
    StreamingMixin,
    ActionsMixin,
):
    @property
    def recording_session_id(self) -> int:
        return self.session_controller.session_id

    @property
    def stream_inserted_text(self) -> str:
        return self.session_controller.committed_text

    @property
    def stream_inserted_any(self) -> bool:
        return self.session_controller.inserted_any

    def __init__(self, root: tk.Tk, *, services: Optional[ApplicationServices] = None):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x780")
        self.root.minsize(900, 700)

        self.settings = SettingsStore.load()
        if re.match(r"^\d+x\d+(?:[+-]\d+[+-]\d+)?$", self.settings.window_geometry or ""):
            self.root.geometry(self.settings.window_geometry)
        # The current version is realtime-only. Older saved settings may contain "Выкл"
        # or "Только превью" from previous builds; force the product behavior requested by user.
        self.settings.realtime_streaming_mode = "Вставлять фрагментами"
        if IS_WINDOWS:
            self.settings.launch_at_startup = is_windows_startup_enabled()
        log_info(
            "Application settings initialized",
            settings={
                "mode": self.settings.mode,
                "language": self.settings.language,
                "privacy_mode": self.settings.privacy_mode,
                "hotkey": self.settings.hotkey,
                "whisper_model": self.settings.whisper_model,
                "recognition_quality": self.settings.recognition_quality,
                "inference_device": self.settings.inference_device,
                "compute_type": self.settings.compute_type,
                "use_vad_filter": self.settings.use_vad_filter,
                "deep_grammar": self.settings.deep_grammar,
                "realtime_chunk_seconds": self.settings.realtime_chunk_seconds,
                "realtime_speed_profile": self.settings.realtime_speed_profile,
                "launch_at_startup": self.settings.launch_at_startup,
                "settings_path": str(SETTINGS_PATH),
            },
        )
        self.services = services or build_default_application_services()
        self.session_controller = HeadlessSessionController(
            self.services.recorder,
            self.services.transcriber,
            self.services.cleaner,
        )
        self.recorder = self.services.recorder
        self.transcriber = self.services.transcriber
        self.cleaner = self.services.cleaner
        self.notifications = self.services.notification_factory(root)
        self._load_notification_position_from_settings()
        self.notifications.on_position_changed = self._on_notification_position_changed
        self.tray = self.services.tray_factory(self)
        self.tray_started = False
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_wav_path: Optional[Path] = None
        self.record_started_at: Optional[float] = None
        self.timer_job: Optional[str] = None
        self.hotkey_handle: object = None
        self.hotkey_poll_stop_event = threading.Event()
        self.hotkey_poll_thread: Optional[threading.Thread] = None
        self.hotkey_poll_hotkey = ""
        self.processing_origin = "main"
        self.last_result_ready = False
        self.paste_target: Optional[PasteTarget] = None
        self.finalizing_recording = False
        self.streaming_stop_event = threading.Event()
        self.stream_context_reset_event = threading.Event()
        self.streaming_thread: Optional[threading.Thread] = None
        self.stream_last_frame_index = 0
        self.stream_preview_raw_text = ""
        self.stream_preview_clean_text = ""
        self.hotkey_entry_capture_active = False
        self.hotkey_entry_pressed: set[str] = set()
        self.hotkey_entry_pressed_order: list[str] = []
        self.hotkey_entry_apply_job: Optional[str] = None
        self.hotkey_entry_previous_value = ""
        self.hotkey_entry_cleared_by_user = False
        self.microphone_apply_job: Optional[str] = None
        self.transcriber_warmup_job: Optional[str] = None
        self.pending_hotkey_start_target: Optional[PasteTarget] = None
        self.pending_hotkey_start_requested = False
        self.exit_requested = False
        self._suspend_hotkey_auto_apply = False
        self._suspend_microphone_auto_apply = False
        self.recording_start_in_progress = False
        self.hotkey_ignore_until = 0.0
        self.hotkey_last_handled_at = 0.0
        self.hotkey_last_accepted_at = 0.0
        self.hotkey_last_poll_press_at = 0.0
        self.hotkey_debug_sequence = 0
        self.hotkey_poll_generation = 0

        self.mode_var = tk.StringVar(value=self.settings.mode)
        self.language_var = tk.StringVar(value=self.settings.language)
        self.privacy_var = tk.BooleanVar(value=self.settings.privacy_mode)
        self.microphone_var = tk.StringVar(value=self.settings.microphone_label)
        self.microphone_options: list[tuple[int, str]] = []
        self.hotkey_var = tk.StringVar(value=self.settings.hotkey)
        self.pretty_hotkey_var = tk.StringVar(value=pretty_hotkey(self.settings.hotkey))
        self.auto_paste_hotkey_var = tk.BooleanVar(value=self.settings.auto_paste_after_hotkey)
        self.insert_edited_text_var = tk.BooleanVar(value=self.settings.insert_edited_text)
        self.show_notifications_var = tk.BooleanVar(value=self.settings.show_notifications)
        self.launch_at_startup_var = tk.BooleanVar(value=self.settings.launch_at_startup)
        self.whisper_model_var = tk.StringVar(value=self.settings.whisper_model if self.settings.whisper_model in WHISPER_MODEL_OPTIONS else LOCAL_WHISPER_MODEL)
        self.recognition_quality_var = tk.StringVar(value=self.settings.recognition_quality if self.settings.recognition_quality in QUALITY_OPTIONS else "Максимальная точность")
        self.inference_device_var = tk.StringVar(value=self.settings.inference_device if self.settings.inference_device in INFERENCE_DEVICE_OPTIONS else "auto")
        self.compute_type_var = tk.StringVar(value=self.settings.compute_type if self.settings.compute_type in COMPUTE_TYPE_OPTIONS else "auto")
        self.use_vad_filter_var = tk.BooleanVar(value=self.settings.use_vad_filter)
        self.custom_terms_var = tk.StringVar(value=self.settings.custom_terms)
        self.deep_grammar_var = tk.BooleanVar(value=self.settings.deep_grammar)
        self.realtime_streaming_mode_var = tk.StringVar(
            value="Вставлять фрагментами"
        )
        self.realtime_chunk_seconds_var = tk.StringVar(value=str(self.settings.realtime_chunk_seconds))
        self.realtime_fast_quality_var = tk.BooleanVar(value=self.settings.realtime_fast_quality)
        self.realtime_speed_profile_var = tk.StringVar(value=self.settings.realtime_speed_profile if self.settings.realtime_speed_profile in STREAMING_SPEED_OPTIONS else "Быстрее")
        self.status_var = tk.StringVar(value="Готово")
        self.timer_var = tk.StringVar(value="00:00")

        self._build_ui()
        self._bind_auto_apply_controls()
        self._setup_hotkey()
        self._poll_worker_queue()
        self._poll_recording_state_watchdog()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tray_started = self.tray.start()
        self._schedule_warmup_transcriber(1200)
