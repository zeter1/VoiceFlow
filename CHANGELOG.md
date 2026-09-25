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

### Corrective Architecture 2.1 test update

- Обновлён repository contract для APP_DIR: после декомпозиции canonical owner пути приложения находится в config.py, а не в compatibility runtime.py.
- Первый Architecture 2.1 CI run подтвердил compile и остальные 27 тестов; stale structural oracle исправлен без изменения runtime behavior.

### Packaged self-test hardening

- voiceflow.py дополнительно упрощён: теперь он импортирует только startup entrypoint.
- GUI/app import graph перенесён за --self-test gate; packaged self-test ловит import regressions и пишет structured JSON evidence.
- GitHub Actions smoke-test получил bounded 90-second timeout, принудительное завершение зависшего EXE и вывод self-test payload.
- Это устраняет возможность бесконечно ждать windowed PyInstaller error dialog при import-time regression.

### Compatibility facade regression guard

- Packaged self-test локализовал ImportError: runtime facade не реэкспортировал HOTKEY_START_GUARD_SECONDS.
- Константа возвращена в compatibility surface.
- Добавлен AST contract, который проверяет, что все явные app imports из runtime действительно существуют в facade до запуска packaging.

## 2026-09-25 — Architecture 2.2: Runtime Facade Retirement

- Удалены все внутренние imports из voiceflow_app/runtime.py.
- services/audio.py, services/transcription.py, services/text_cleaner.py, ui/notifications.py и ui/tray.py переведены с wildcard runtime imports на явные canonical owners.
- app/main_window.py, app/recording.py и app/hotkeys.py также переведены с compatibility facade на config/diagnostics/dependencies/hotkey_config/settings/windows.
- Во всём voiceflow_app теперь запрещены wildcard imports.
- runtime.py сокращён до внешнего re-export-only compatibility facade без собственной implementation logic и внутренних consumers.
- Repository contracts механически запрещают возврат internal runtime dependencies и implementation внутрь facade.
- Windows package пересобирается для проверки нового import graph.

## 2026-09-25 — Architecture 2.3: Service Contracts & Dependency Inversion

- Audio-device enumeration вынесена из dependencies.py в отдельный audio_devices.py с injectable backend и offline fake-device tests.
- CUDA/DLL/subprocess probing вынесен из LocalTranscriber в cuda_runtime.py; transcriber получает backend_runtime и model_factory через явные injection seams.
- Windows registry startup и foreground/paste implementation разделены на windows_startup.py и windows_insertion.py; windows.py сохранён как совместимый re-export facade.
- Добавлены services/contracts.py с Protocol-контрактами для AudioRecorder, LocalTranscriber и LocalTextCleaner.
- Добавлены offline regression tests для microphone catalog, CUDA backend policy/preflight cache и соответствия concrete services объявленным contracts.
- Repository guards запрещают возвращать device discovery в dependencies.py и CUDA environment probing внутрь transcription service.
- Пользовательское runtime-поведение намеренно не менялось; Windows binary пересобирается для проверки нового dependency graph.

## 2026-09-25 — Architecture 2.4: Application Ports & Headless Session Controller

- Создан composition.py с injectable ApplicationServices; VoiceFlowOfflineApp больше не создаёт AudioRecorder/LocalTranscriber/LocalTextCleaner/NotificationManager/TrayManager напрямую.
- Concrete desktop services загружаются лениво в composition root, поэтому composition/session modules импортируются в offline tests без Tk/PortAudio/Whisper runtime.
- Создан app/session_controller.py: headless owner session_id, capture lifecycle, stable committed text и service pipeline frames -> WAV -> transcription -> optional cleanup.
- Realtime stable commits теперь записываются через session controller; streaming frames -> WAV -> Whisper orchestration также проходит через него.
- Recording start/stop/recovery делегируют recorder lifecycle session controller вместо прямого управления AudioRecorder из Tk mixin.
- Добавлены headless tests start -> frames -> transcription -> commit -> stop, double-start race, stop/restart, failed-start recovery, stop failure и finalizing race.
- Исправлен latent Architecture 2.3 regression: recording warm-up больше не вызывает удалённый private _windows_missing_cuda_dlls; добавлен public backend_candidates contract.
- Packaged self-test теперь строит default ApplicationServices и проверяет composition graph без открытия GUI.

### Architecture 2.4 stop-order preservation

- Session controller теперь умеет раздельно stop capture и discard buffered frames.
- RecordingMixin сохраняет прежний realtime порядок: stop microphone -> signal streaming worker -> discard frames, чтобы не увеличивать риск потери последнего доступного chunk при stop race.
- Добавлен regression test на deferred frame discard.

## 2026-09-25 — Architecture 2.5: Headless Realtime Policy & Queue Contracts

- Realtime timing/profile selection and chunk commit/cancellation decisions extracted from app/streaming.py into pure core/realtime_policy.py.
- Audio signal statistics moved into services/audio_analysis.py; NumPy is imported lazily only for production frame analysis, while thresholds/stat summaries have dependency-free regression tests.
- app/streaming.py now evaluates audio statistics once per candidate chunk instead of recomputing the same metrics for probable-speech detection.
- Main-thread queue decode/stale-session/UI routing moved from streaming.py into app/worker_dispatch.py.
- Added typed worker_messages.py contract with explicit message kinds/payloads, legacy tuple coercion and pure stale-session/after-stop gates.
- Hotkey polling, tray callbacks and streaming workers now enqueue typed worker messages instead of ad-hoc tuple literals.
- Added regression tests for timing profiles, pause/max-duration triggers, quiet/bad chunk advancement, final-chunk cancellation, noisy-room pause statistics and stale session filtering.
- Repository contracts now bound both streaming.py and worker_dispatch.py and prevent direct tuple queue producers from returning in critical producers.
