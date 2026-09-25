# Изменения Windows-сборки

## Модульная архитектура VoiceFlow

- Внутренний монолит voiceflow.py разделён на пакет voiceflow_app: runtime, services, UI infrastructure и отдельные зоны main-window behavior.
- voiceflow.py остаётся маленьким совместимым launcher, поэтому привычный запуск и PyInstaller entrypoint сохраняются.
- Отдельно вынесены microphone capture, faster-whisper transcription, text cleanup, notifications и tray.
- Realtime/hotkey/recording/insertion код разделён по ответственности для более безопасных исправлений и code review.
- Сохранено прежнее расположение voiceflow_logs и voiceflow_settings для source и VoiceFlow.exe.
- Добавлены structural regression tests и расширенная AI-oriented документация.

## Проверка сборки

GitHub Actions компилирует voiceflow.py + voiceflow_app + tests, запускает offline contracts, собирает portable VoiceFlow.exe, выполняет packaged --self-test и публикует ZIP + SHA-256.

Реальный микрофон, глобальные hotkeys, вставка в сторонние приложения, загрузка Whisper-модели и CUDA на пользовательском GPU требуют отдельной Windows runtime-проверки.
