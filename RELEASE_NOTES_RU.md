# Изменения Windows-сборки

## Architecture 2.5 — Headless Realtime Pipeline & Queue Contracts

- Вынесены pure realtime timing/commit/cancellation decisions из большого streaming.py.
- Аудиостатистика вынесена в отдельный audio-analysis owner; NumPy загружается лениво только при настоящем анализе frames.
- Убрано повторное вычисление одних и тех же audio metrics перед commit decision.
- Main-thread worker dispatch и stale-session UI routing вынесены в отдельный app/worker_dispatch.py.
- Worker queue получил явный typed contract вместо набора неформальных строк/tuple payloads.
- Stale results от старой recording session и поздние результаты после stop теперь проходят через отдельный тестируемый gate.
- Hotkey polling, tray callbacks и realtime worker используют единый queue-message API.
- Добавлены offline regression tests для pause/noise/forced commit/final cancellation/session restart behavior.

## Проверка сборки

GitHub Actions выполняет compile, полный offline regression/architecture suite, PyInstaller build, bounded packaged VoiceFlow.exe --self-test, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, CUDA inference, global hotkey, tray и вставка текста в сторонние Windows-приложения требуют отдельной интерактивной runtime-проверки.
