# Изменения Windows-сборки

## Architecture 2.3 — Service Contracts & Dependency Inversion

- Перечисление микрофонов отделено от общего dependency loader и теперь имеет тестируемый audio adapter.
- LocalTranscriber больше не занимается прямым поиском CUDA DLL и запуском environment preflight: это вынесено в отдельный CUDA runtime adapter.
- Для transcriber добавлены injection seams backend_runtime/model_factory, что снижает coupling и позволяет проверять backend policy без GPU.
- Windows autostart registry и foreground/paste code физически разделены на независимые adapters; старый windows import path сохранён для совместимости.
- Для AudioRecorder, LocalTranscriber и LocalTextCleaner добавлены явные Protocol contracts.
- Добавлены offline regression tests, не требующие микрофона, GPU, Whisper-модели или Windows Registry.

## Проверка сборки

GitHub Actions выполняет compile, offline regression/architecture tests, PyInstaller build, bounded packaged VoiceFlow.exe --self-test, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, CUDA inference, global hotkey, tray и вставка текста в сторонние Windows-приложения требуют отдельной интерактивной runtime-проверки.
