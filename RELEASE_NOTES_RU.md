# Изменения Windows-сборки

## Architecture 2.0 — Testable Realtime Core

- Критические решения realtime-ввода по deduplication, пунктуации, continuation/final-tail и trailing voice commands вынесены в отдельное чистое ядро.
- Recording state и hotkey edge/debounce получили отдельные deterministic state-machine модули.
- Добавлены regression tests, которые проверяют эти правила без запуска GUI, микрофона и Whisper.
- Hotkey polling теперь использует тестируемую edge state machine, сохраняя защиту от дребезга и слишком близких повторных нажатий.
- Убраны wildcard context imports из main_window, recording и hotkeys; transitional bridge остаётся только для ещё не мигрированных legacy mixins.
- Обучающие инструкции перенесены в docs/user-guide/, отдельно от инженерной и AI-документации.

- Исправлен mechanical extraction artifact первого Architecture 2.0 commit; финальная сборка повторно проходит compile/test/package gates.

## Проверка сборки

GitHub Actions компилирует весь package и tests, выполняет offline regressions, собирает VoiceFlow.exe, запускает packaged --self-test и публикует portable ZIP + SHA-256.

Реальный микрофон, глобальные hotkeys в пользовательской Windows-сессии, вставка в сторонние приложения, загрузка Whisper-модели и CUDA на пользовательском GPU требуют отдельной runtime-проверки.
