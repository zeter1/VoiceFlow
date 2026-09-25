"""Main-window construction, state snapshots and notifications.

Methods keep their original implementation while the physical module boundary
makes navigation, review and future extraction safer.
"""

from __future__ import annotations

from ..context import *  # noqa: F401,F403 - compatibility surface for extracted methods


class UiMixin:
    def _build_ui(self) -> None:
        pad = 12
        root = ttk.Frame(self.root, padding=pad)
        root.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(root, text="VoiceFlow Offline", font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            root,
            text="Локальная диктовка без OpenAI API key: запись → усиленная правка текста → вставка в активное поле.",
        )
        subtitle.pack(anchor="w", pady=(2, 12))

        controls = ttk.Frame(root)
        controls.pack(fill=tk.X, pady=(0, 10))

        self.record_btn = ttk.Button(controls, text="● Начать запись", command=lambda: self.toggle_recording("main"))
        self.record_btn.pack(side=tk.LEFT)

        ttk.Label(controls, textvariable=self.timer_var).pack(side=tk.LEFT, padx=(12, 16))
        ttk.Label(controls, text="Статус:").pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_var).pack(side=tk.LEFT, padx=(5, 20))

        ttk.Label(controls, text="Модель:").pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.whisper_model_var).pack(side=tk.LEFT, padx=(4, 20))

        settings = ttk.LabelFrame(root, text="Настройки", padding=pad)
        settings.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(settings, text="Режим редактирования:").grid(row=0, column=0, sticky="w")
        mode_box = ttk.Combobox(
            settings,
            textvariable=self.mode_var,
            values=[
                "Точно как сказано",
                "Чистый текст",
                "Деловой стиль",
                "Коротко",
                "Развернуто",
                "Продающий стиль",
                "Для ChatGPT / AI-промпт",
                "Для кода",
            ],
            state="readonly",
            width=30,
        )
        mode_box.grid(row=0, column=1, padx=(8, 24), sticky="w")
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Label(settings, text="Язык:").grid(row=0, column=2, sticky="w")
        lang_box = ttk.Combobox(
            settings,
            textvariable=self.language_var,
            values=["auto", "ru", "en", "es", "fr", "de"],
            state="readonly",
            width=10,
        )
        lang_box.grid(row=0, column=3, padx=(8, 24), sticky="w")
        lang_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Checkbutton(
            settings,
            text="Privacy mode (WAV)",
            variable=self.privacy_var,
            command=self._save_settings,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(settings, text="Микрофон:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.microphone_box = ttk.Combobox(
            settings,
            textvariable=self.microphone_var,
            state="readonly",
            width=76,
        )
        self.microphone_box.grid(
            row=1,
            column=1,
            columnspan=3,
            padx=(8, 24),
            pady=(10, 0),
            sticky="we",
        )
        self.microphone_box.bind("<<ComboboxSelected>>", lambda _event: self._schedule_microphone_auto_apply(immediate=True))

        ttk.Button(
            settings,
            text="Обновить",
            command=lambda: self.refresh_microphones(show_message=True),
        ).grid(row=1, column=4, sticky="w", pady=(10, 0))

        ttk.Label(settings, text="Горячая клавиша:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        hotkey_frame = ttk.Frame(settings)
        hotkey_frame.grid(row=2, column=1, columnspan=4, sticky="we", pady=(10, 0))

        self.hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var, width=24)
        self.hotkey_entry.pack(side=tk.LEFT)
        self.hotkey_entry.bind("<FocusIn>", self._begin_hotkey_entry_capture)
        self.hotkey_entry.bind("<FocusOut>", self._end_hotkey_entry_capture)
        self.hotkey_entry.bind("<KeyPress>", self._capture_hotkey_entry_keypress)
        self.hotkey_entry.bind("<KeyRelease>", self._capture_hotkey_entry_keyrelease)
        ttk.Button(hotkey_frame, text="Нажать сочетание", command=self.capture_hotkey).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(hotkey_frame, text="Ctrl+Shift+Space", command=lambda: self.set_hotkey_preset("ctrl+shift+space")).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(hotkey_frame, text="Очистить", command=self.clear_hotkey_field).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(hotkey_frame, text="Сейчас: ").pack(side=tk.LEFT, padx=(16, 0))
        ttk.Label(hotkey_frame, textvariable=self.pretty_hotkey_var).pack(side=tk.LEFT)

        recognition = ttk.LabelFrame(settings, text="Точность распознавания", padding=8)
        recognition.grid(row=3, column=0, columnspan=5, sticky="we", pady=(12, 0))

        ttk.Label(recognition, text="Whisper-модель:").grid(row=0, column=0, sticky="w")
        model_box = ttk.Combobox(
            recognition,
            textvariable=self.whisper_model_var,
            values=WHISPER_MODEL_OPTIONS,
            state="readonly",
            width=14,
        )
        model_box.grid(row=0, column=1, sticky="w", padx=(8, 18))
        model_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(recognition, text="Качество:").grid(row=0, column=2, sticky="w")
        quality_box = ttk.Combobox(
            recognition,
            textvariable=self.recognition_quality_var,
            values=QUALITY_OPTIONS,
            state="readonly",
            width=22,
        )
        quality_box.grid(row=0, column=3, sticky="w", padx=(8, 18))
        quality_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Checkbutton(
            recognition,
            text="VAD: обрезать тишину и шумные паузы",
            variable=self.use_vad_filter_var,
            command=self._save_settings,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(recognition, text="Устройство:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        device_box = ttk.Combobox(
            recognition,
            textvariable=self.inference_device_var,
            values=INFERENCE_DEVICE_OPTIONS,
            state="readonly",
            width=14,
        )
        device_box.grid(row=1, column=1, sticky="w", padx=(8, 18), pady=(8, 0))
        device_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(recognition, text="Compute:").grid(row=1, column=2, sticky="w", pady=(8, 0))
        compute_box = ttk.Combobox(
            recognition,
            textvariable=self.compute_type_var,
            values=COMPUTE_TYPE_OPTIONS,
            state="readonly",
            width=16,
        )
        compute_box.grid(row=1, column=3, sticky="w", padx=(8, 18), pady=(8, 0))
        compute_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(recognition, text="Словарь терминов:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        terms_entry = ttk.Entry(recognition, textvariable=self.custom_terms_var)
        terms_entry.grid(row=2, column=1, columnspan=4, sticky="we", padx=(8, 0), pady=(8, 0))
        terms_entry.bind("<FocusOut>", lambda _event: self._save_settings())
        recognition.columnconfigure(4, weight=1)

        streaming = ttk.LabelFrame(settings, text="Realtime-ввод текста", padding=8)
        streaming.grid(row=4, column=0, columnspan=5, sticky="we", pady=(12, 0))

        ttk.Label(streaming, text="Режим:").grid(row=0, column=0, sticky="w")
        ttk.Label(streaming, text="Только realtime-вставка фрагментами").grid(row=0, column=1, sticky="w", padx=(8, 18))

        ttk.Label(streaming, text="Интервал фрагмента:").grid(row=0, column=2, sticky="w")
        streaming_interval_box = ttk.Combobox(
            streaming,
            textvariable=self.realtime_chunk_seconds_var,
            values=STREAMING_INTERVAL_OPTIONS,
            state="readonly",
            width=8,
        )
        streaming_interval_box.grid(row=0, column=3, sticky="w", padx=(8, 18))
        streaming_interval_box.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())

        ttk.Checkbutton(
            streaming,
            text="Стриминг в ускоренном режиме: быстрее, но менее точно",
            variable=self.realtime_fast_quality_var,
            command=self._transcriber_settings_changed,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(streaming, text="Профиль realtime:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        speed_profile_box = ttk.Combobox(
            streaming,
            textvariable=self.realtime_speed_profile_var,
            values=STREAMING_SPEED_OPTIONS,
            state="readonly",
            width=14,
        )
        speed_profile_box.grid(row=1, column=1, sticky="w", padx=(8, 18), pady=(8, 0))
        speed_profile_box.bind("<<ComboboxSelected>>", self._transcriber_settings_changed)

        ttk.Label(
            streaming,
            text=(
                "Оптимально для твоего Ryzen 5 5600X + GTX 1660 Super: устройство auto/cuda, compute auto/int8_float16, "
                "интервал 1–2 сек, профиль «Быстрее»."
            ),
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))
        streaming.columnconfigure(4, weight=1)

        behavior = ttk.LabelFrame(settings, text="Поведение после записи", padding=8)
        behavior.grid(row=5, column=0, columnspan=5, sticky="we", pady=(12, 0))

        ttk.Label(
            behavior,
            text="✓ Ввод только в realtime: фрагменты вставляются во время записи, финальная вставка после остановки отключена.",
        ).grid(row=0, column=0, columnspan=4, sticky="w")

        ttk.Checkbutton(
            behavior,
            text="Вставлять отредактированный текст: очистка, пунктуация и выбранный режим",
            variable=self.insert_edited_text_var,
            command=self._save_settings,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Checkbutton(
            behavior,
            text="Глубокая грамматика: дополнительные правила + LanguageTool, если установлен",
            variable=self.deep_grammar_var,
            command=self._save_settings,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Checkbutton(
            behavior,
            text="Показывать уведомления: запись / остановка / распознавание / вставка",
            variable=self.show_notifications_var,
            command=self._save_settings,
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        startup_state = tk.NORMAL if IS_WINDOWS else tk.DISABLED
        ttk.Checkbutton(
            behavior,
            text="Запускать программу при старте Windows 11",
            variable=self.launch_at_startup_var,
            command=self.apply_startup_setting,
            state=startup_state,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))

        ttk.Label(
            behavior,
            text="Рабочий сценарий: нажми горячую клавишу → говори → ставь курсор в любое поле/окно — realtime-текст пойдёт туда, где сейчас курсор.",
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))

        settings.columnconfigure(1, weight=1)
        self.refresh_microphones(show_message=False)

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True)

        raw_frame = ttk.LabelFrame(body, text="Распознанный текст без редактирования", padding=8)
        raw_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.raw_text = tk.Text(raw_frame, height=7, wrap=tk.WORD)
        self.raw_text.pack(fill=tk.BOTH, expand=True)

        clean_frame = ttk.LabelFrame(body, text="Готовый отредактированный текст", padding=8)
        clean_frame.pack(fill=tk.BOTH, expand=True)
        self.clean_text = tk.Text(clean_frame, height=9, wrap=tk.WORD)
        self.clean_text.pack(fill=tk.BOTH, expand=True)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(12, 0))

        ttk.Button(actions, text="Скопировать отредактированный", command=lambda: self.copy_result(show_messages=True, edited=True)).pack(side=tk.LEFT)
        ttk.Button(actions, text="Скопировать без редактирования", command=lambda: self.copy_result(show_messages=True, edited=False)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Скопировать для вставки", command=self.paste_result).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Очистить", command=self.clear_texts).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="Свернуть окно", command=self.hide_main_window).pack(side=tk.RIGHT)

        help_text = (
            "Первый запуск модели может занять несколько минут: она скачивается один раз. "
            "Если горячая клавиша не работает, запусти PowerShell/программу от имени администратора. Настройки сохраняются в voiceflow_settings, а логи каждого запуска — в отдельной папке voiceflow_logs/run_дата_время. Последний запуск дублируется в voiceflow_logs/_last_run. Для усиленной грамматики: pip install language-tool-python"
        )
        ttk.Label(root, text=help_text).pack(anchor="w", pady=(10, 0))

    def _bind_auto_apply_controls(self) -> None:
        self.microphone_var.trace_add("write", self._schedule_microphone_auto_apply)
        self.hotkey_var.trace_add("write", self._schedule_hotkey_auto_apply)

    def _state_snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        try:
            stream_alive = self.streaming_thread is not None and self.streaming_thread.is_alive()
        except Exception:
            stream_alive = False
        return {
            "session_id": self.recording_session_id,
            "is_recording": bool(self.recorder.is_recording),
            "finalizing": bool(self.finalizing_recording),
            "recording_start_in_progress": bool(self.recording_start_in_progress),
            "pending_hotkey_start": bool(self.pending_hotkey_start_requested),
            "hotkey_ignore_remaining": max(0.0, round(self.hotkey_ignore_until - now, 3)),
            "hotkey_last_handled_age": round(now - self.hotkey_last_handled_at, 3) if self.hotkey_last_handled_at else None,
            "hotkey_last_accepted_age": round(now - self.hotkey_last_accepted_at, 3) if self.hotkey_last_accepted_at else None,
            "hotkey_last_poll_press_age": round(now - self.hotkey_last_poll_press_at, 3) if self.hotkey_last_poll_press_at else None,
            "hotkey_poll_hotkey": self.hotkey_poll_hotkey,
            "hotkey_poll_thread_alive": bool(self.hotkey_poll_thread is not None and self.hotkey_poll_thread.is_alive()),
            "status": self.status_var.get(),
            "record_button": str(self.record_btn.cget("text")) if hasattr(self, "record_btn") else "",
            "streaming_thread_alive": stream_alive,
            "stream_inserted_any": bool(self.stream_inserted_any),
            "notification_kind": getattr(self.notifications, "_last_kind", ""),
        }

    def log_state(self, category: str, event: str, **context: object) -> None:
        context.setdefault("state", self._state_snapshot())
        log_category(category, event, **context)

    def _next_hotkey_debug_sequence(self) -> int:
        self.hotkey_debug_sequence += 1
        return self.hotkey_debug_sequence

    def _hotkey_target_snapshot(self, target: Optional[PasteTarget] = None) -> dict[str, object]:
        if target is None:
            try:
                target = get_paste_target()
            except Exception:
                target = None
        return {
            "foreground_hwnd": getattr(target, "foreground_hwnd", None),
            "focus_hwnd": getattr(target, "focus_hwnd", None),
        }

    def _load_notification_position_from_settings(self) -> None:
        try:
            x = self.settings.notification_x
            y = self.settings.notification_y
            if isinstance(x, int) and isinstance(y, int):
                self.notifications.manual_position = (x, y)
                log_info("Notification position loaded from settings", x=x, y=y)
        except Exception as exc:
            log_exception("Could not load notification position from settings", exc)

    def _on_notification_position_changed(self, position: Optional[tuple[int, int]]) -> None:
        try:
            if position is None:
                self.settings.notification_x = None
                self.settings.notification_y = None
            else:
                self.settings.notification_x = int(position[0])
                self.settings.notification_y = int(position[1])
            SettingsStore.save(self.settings)
            log_info("Notification position saved", position=position)
        except Exception as exc:
            log_exception("Could not save notification position", exc, position=position)

    def log_hotkey_trace(self, event: str, **context: object) -> None:
        """Write a very detailed hotkey trace for repeat-start/stop bugs."""
        context.setdefault("debug_seq", self._next_hotkey_debug_sequence())
        context.setdefault("hotkey", normalize_hotkey(self.hotkey_var.get()))
        context.setdefault("thread", threading.current_thread().name)
        context.setdefault("state", self._state_snapshot())
        log_category("hotkey_trace", event, **context)

    def notify(
        self,
        message: str,
        kind: str = "idle",
        duration_ms: Optional[int] = 2200,
        force_recreate: bool = False,
    ) -> None:
        self.log_state(
            "notifications",
            "notify_requested",
            kind=kind,
            duration_ms=duration_ms,
            enabled=bool(self.show_notifications_var.get()),
            message=message,
            force_recreate=force_recreate,
        )
        if self.show_notifications_var.get():
            self.notifications.show(message, kind=kind, duration_ms=duration_ms, force_recreate=force_recreate)
            if kind != "recording" and self.recorder.is_recording and duration_ms is not None:
                try:
                    self.root.after(max(100, int(duration_ms) + 80), self._restore_recording_notification_if_needed)
                except Exception:
                    pass

    def _format_recording_notification_message(self) -> str:
        if self.record_started_at is None:
            current = "00:00"
        else:
            elapsed = max(0, int(time.time() - self.record_started_at))
            minutes, seconds = divmod(elapsed, 60)
            current = f"{minutes:02d}:{seconds:02d}"
        return f"🎙 Идёт запись: {current}\nНажми {pretty_hotkey(self.hotkey_var.get())} ещё раз, чтобы остановить"

    def _show_recording_notification(self, force_recreate: bool = False) -> None:
        if not self.show_notifications_var.get() or not self.recorder.is_recording:
            return
        self.notifications.reset_manual_position_if_mostly_offscreen()
        self.notifications.show(
            self._format_recording_notification_message(),
            kind="recording",
            duration_ms=None,
            force_recreate=force_recreate,
        )

    def _restore_recording_notification_if_needed(self) -> None:
        if self.recorder.is_recording and not self.exit_requested:
            self._show_recording_notification(force_recreate=False)
