# Изменения Windows-сборки

## Architecture 2.7 — Headless Realtime Text Commit & Insertion Decisions

- Очистка realtime текста, dedupe, punctuation-at-commit, trailing voice-command split и выбор точного текста для вставки вынесены в отдельный headless `RealtimeTextPipeline`.
- `app/streaming.py` больше не содержит набор proxy-обёрток вокруг pure realtime text logic; там остаются UI, реальная вставка и выполнение voice-control side effects.
- Worker dispatch сначала получает тестируемый `RealtimeResultPlan`, и только затем меняет UI или вставляет текст.
- Исправлена потеря `commit_meta`: фактические sentence-pause / forced-commit / Whisper-end metadata теперь доходят до punctuation decision перед вставкой.
- Добавлены offline regression tests для exact paste payload, raw-vs-cleaned selection, continuation casing, pause punctuation, dedupe, preview/no-paste и trailing send-command separation.
- Пользовательская логика вставки и voice commands сохранена; изменён ownership и исправлена передача metadata.

## Проверка сборки

GitHub Actions выполняет compile, полный offline regression/architecture suite, PyInstaller build, packaged `VoiceFlow.exe --self-test`, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, Whisper/CUDA, global hotkey и вставка в живые Windows-приложения требуют отдельной интерактивной runtime-проверки.
