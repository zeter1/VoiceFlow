# Изменения Windows-сборки

## Architecture 2.1 — явные зависимости и декомпозиция runtime

- Полностью удалён transitional context.py.
- Основные app-модули и startup entrypoint больше не получают зависимости через wildcard import.
- Большой runtime.py разделён по ответственности: config, diagnostics, dependencies, hotkey config, settings, voice commands и Windows adapters.
- runtime.py теперь небольшой compatibility facade, а не место для бизнес-логики.
- Добавлены автоматические architecture guards, которые защищают новые границы от обратного слияния в монолит.
- Сохранены прежние пути пользовательских logs/settings и старый запуск python voiceflow.py.

- Исправлен stale architecture-test oracle: APP_DIR/get_app_dir теперь корректно проверяется в config.py после декомпозиции runtime.

## Проверка сборки

GitHub Actions должен выполнить compile, offline regression/architecture tests, PyInstaller build, packaged VoiceFlow.exe --self-test, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, CUDA, глобальная горячая клавиша и вставка в сторонние Windows-приложения требуют отдельной интерактивной runtime-проверки.
