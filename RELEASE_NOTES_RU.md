# Изменения Windows-сборки

## Architecture 2.2 — Runtime Facade Retirement

- Все внутренние модули VoiceFlow теперь импортируют зависимости напрямую из canonical owner-модулей.
- Убраны последние wildcard imports из services и UI infrastructure.
- app/main_window.py, recording.py и hotkeys.py больше не зависят от compatibility runtime facade.
- runtime.py оставлен только как внешний совместимый import path и содержит исключительно re-export существующих owners.
- Добавлены architecture guards, запрещающие wildcard imports и внутреннюю зависимость от runtime.py.
- Поведение записи, realtime insertion, hotkeys, settings и пользовательские пути logs/settings намеренно не менялось.

## Проверка сборки

GitHub Actions выполняет compile, offline regression/architecture tests, PyInstaller build, bounded packaged VoiceFlow.exe --self-test, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, CUDA, глобальная горячая клавиша, tray и вставка в сторонние Windows-приложения требуют отдельной интерактивной runtime-проверки.
