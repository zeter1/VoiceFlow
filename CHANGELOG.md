# CHANGELOG

## 2026-09-25 — модульная архитектура и AI-навигация

- Монолитный voiceflow.py разделён на пакет voiceflow_app: runtime, services, ui и app boundaries.
- VoiceFlowOfflineApp разделён на UI/controls/hotkeys/recording/streaming/actions mixins без намеренного изменения пользовательского поведения.
- voiceflow.py оставлен тонким совместимым entrypoint для команды запуска и PyInstaller.
- Сохранён source/frozen contract расположения voiceflow_logs и voiceflow_settings.
- CI теперь компилирует весь package и проверяет structural repository contracts.
- Добавлены AGENTS.md, AI_CONTEXT, DEVELOPMENT и обновлённая архитектурная карта.
- Для проходки запускается обновление Windows portable binary и packaged self-test.
