# Изменения Windows-сборки

## Architecture 2.4 — Application Ports & Headless Session Tests

- VoiceFlowOfflineApp получает сервисы через injectable ApplicationServices вместо прямого создания recorder/transcriber/cleaner/notification/tray implementations.
- Добавлен headless session controller для capture lifecycle, session identity и realtime committed text.
- Realtime frames → temporary WAV → Whisper path и stable commit state теперь имеют отдельный application-layer seam, тестируемый без GUI.
- Добавлены regression tests для полного headless сценария start → frames → transcription → stable commit → stop, повторного старта, race/finalizing и failure/recovery.
- Исправлена скрытая warm-up проблема после Architecture 2.3: runtime больше не обращается к удалённому private CUDA helper.
- Packaged self-test проверяет новый composition graph до запуска Tk GUI.

## Проверка сборки

GitHub Actions выполняет compile, offline regression/architecture tests, PyInstaller build, bounded packaged VoiceFlow.exe --self-test, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, CUDA inference, global hotkey, tray, уведомления и вставка текста в сторонние Windows-приложения требуют отдельной интерактивной runtime-проверки.
