# Изменения Windows-сборки

- Готовая Windows-сборка VoiceFlow теперь публикуется как постоянный GitHub Release.
- Portable ZIP содержит `VoiceFlow.exe` и packaged runtime для `faster-whisper`, CTranslate2, PyAV, tray/clipboard/hotkey-зависимостей и LanguageTool-интеграции.
- Перед публикацией CI запускает offline regression tests, собирает PyInstaller-дистрибутив и выполняет `--self-test` уже packaged EXE.
- В релиз добавлен отдельный SHA-256 portable-архива.
- README теперь прямо указывает, где скачать готовую Windows-версию и какие внешние условия остаются для Whisper-моделей и NVIDIA GPU.

## Проверка

CI подтверждает packaged imports/runtime на GitHub-hosted Windows runner без открытия GUI и микрофона. Реальная запись с пользовательского аудиоустройства, глобальные hotkeys, вставка в сторонние приложения, загрузка Whisper-модели и GPU-ускорение требуют runtime-проверки на целевом Windows-компьютере.
