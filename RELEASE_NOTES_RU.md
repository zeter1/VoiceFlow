# Изменения Windows-сборки

## Architecture 2.6 — Headless Realtime Worker Engine

- Realtime frame cursor, transcription context и chunk lifecycle вынесены из Tk-oriented streaming mixin в отдельный headless `RealtimeWorkerEngine`.
- Engine отдельно координирует audio policy, frames → transcription, bad/noise filtering, dedupe context, временный WAV cleanup и typed worker messages.
- `app/streaming.py` стал значительно меньше и теперь в основном связывает UI/application settings с headless engine.
- Добавлены regression tests для speech→pause→result, noise→skip→continue, filtered chunk recovery, stop cancellation, context reset и продолжения работы после transcription error.
- Исправлен скрытый error-masking сценарий: исключение распознавания до создания WAV result больше не может быть затёрто ошибкой cleanup неинициализированного `wav_path`.
- Сохранены текущие realtime timing/pause/dedupe/session semantics; пользовательская логика намеренно не менялась.

## Проверка сборки

GitHub Actions выполняет compile, полный offline regression/architecture suite, PyInstaller build, packaged `VoiceFlow.exe --self-test`, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, Whisper inference, CUDA, global hotkey, tray и вставка текста в сторонние Windows-приложения по-прежнему требуют интерактивной runtime-проверки.
