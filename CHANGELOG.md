# CHANGELOG

## 2026-09-25 — модульная архитектура и AI-навигация

- Монолитный voiceflow.py разделён на пакет voiceflow_app: runtime, services, ui и app boundaries.
- VoiceFlowOfflineApp разделён на UI/controls/hotkeys/recording/streaming/actions mixins без намеренного изменения пользовательского поведения.
- voiceflow.py оставлен тонким совместимым entrypoint для команды запуска и PyInstaller.
- Сохранён source/frozen contract расположения voiceflow_logs и voiceflow_settings.
- CI теперь компилирует весь package и проверяет structural repository contracts.
- Добавлены AGENTS.md, AI_CONTEXT, DEVELOPMENT и обновлённая архитектурная карта.
- Для проходки запускается обновление Windows portable binary и packaged self-test.

## 2026-09-25 — Architecture 2.0: Testable Realtime Core

- Realtime dedupe/punctuation/final-tail/voice-command decisions вынесены в независимый voiceflow_app/core/realtime.py.
- Recording state classification вынесена в core/recording_state.py.
- Windows hotkey edge/debounce state machine вынесена в core/hotkey_state.py и подключена к polling/handler runtime.
- Добавлены независимые regression tests без Tkinter, микрофона и Whisper.
- app/main_window.py, app/recording.py и app/hotkeys.py больше не используют wildcard context import.
- Добавлены architecture guards, запрещающие core импортировать runtime/context/Tkinter и возвращать wildcard в новые state owners.
- Пользовательская образовательная документация перенесена из корня docs/ в docs/user-guide/.
- Техническая документация и AI-навигация обновлены под новые boundaries.
- Windows portable binary пересобирается и проходит packaged self-test.

### Corrective validation fix

- Удалён оставшийся фрагмент старой inline hotkey edge-логики, который попал в mechanical extraction и вызвал IndentationError в первом CI run Architecture 2.0.
- Runtime polling теперь имеет один source of truth: core/hotkey_state.py.

## 2026-09-25 — Architecture 2.1: Context Elimination & Runtime Decomposition

- Удалён transitional voiceflow_app/context.py.
- ui.py, controls.py, streaming.py, actions.py и entrypoint.py переведены с wildcard context imports на явные owner imports.
- 1400+ строк runtime.py разделены на config.py, diagnostics.py, dependencies.py, hotkey_config.py, settings.py, voice_commands.py и windows.py.
- runtime.py оставлен только как небольшой backward-compatible facade для ещё не мигрированных legacy services/UI и внешних import paths.
- Добавлены architecture guards: context.py не может вернуться незаметно, app orchestration не допускает wildcard imports, runtime facade ограничен по размеру.
- AI/architecture документация синхронизирована с новыми владельцами.
- Windows portable binary пересобирается после изменения import/package graph.
