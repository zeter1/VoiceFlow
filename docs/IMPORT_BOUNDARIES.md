# VoiceFlow — Import Boundaries

Эта карта предназначена прежде всего для ChatGPT/Codex и code review. Она показывает canonical owner каждой зависимости и не даёт архитектуре снова превратиться в скрытый wildcard/facade graph.

## Главное правило

Внутренний код `voiceflow_app` **не импортирует `runtime.py`**. `runtime.py` — только внешний compatibility path для старых imports. Также запрещены wildcard imports вида `from ... import *`.

## Canonical owners

| Нужна зависимость | Canonical owner |
| --- | --- |
| пути приложения, constants, streaming/hotkey thresholds | `config.py` |
| logs, diagnostics, exception/single-instance helpers | `diagnostics.py` |
| numpy/sounddevice/optional third-party package loading | `dependencies.py` |
| microphone/input-device discovery | `audio_devices.py` |
| CUDA DLL/preflight/backend environment | `cuda_runtime.py` |
| hotkey normalization / Windows VK mapping | `hotkey_config.py` |
| persisted/runtime settings models | `settings.py` |
| voice-command vocabulary/parser | `voice_commands.py` |
| Windows startup registry/command | `windows_startup.py` |
| Windows foreground target/native paste | `windows_insertion.py` |
| historical combined Windows import path | `windows.py` facade |
| deterministic realtime/recording/hotkey decisions | `core/*` |
| microphone/model/text-cleaning behavior | `services/*` |
| tray/toast presentation | `ui/*` |
| default concrete construction | `composition.py` |
| headless session identity/capture/committed text | `app/session_controller.py` |
| notification/tray application ports | `app/ports.py` |
| Tk/application orchestration | remaining `app/*` |

## Dependency direction

- `core/*` → Python stdlib only. Не зависит от Tkinter, runtime facade, services или platform adapters.
- `config.py` → stdlib only.
- `diagnostics.py` → config + stdlib.
- `dependencies.py` → diagnostics + third-party packages; no device enumeration.
- `audio_devices.py` → Protocol/stdlib; production backend imported lazily.
- `cuda_runtime.py` → config + stdlib/subprocess; actual DLL finder is lazy/injectable.
- `hotkey_config.py` → config + stdlib/platform API.
- `settings.py` → config + diagnostics + hotkey_config.
- `voice_commands.py` → stdlib.
- `windows_startup.py` → config + winreg/stdlib.
- `windows_insertion.py` → config + optional pyautogui + Windows/stdlib APIs.
- `windows.py` → re-export only.
- `services/*` → focused owners выше; не app/ui/runtime.
- `ui/*` → config/dependencies/diagnostics/windows по необходимости; UI infrastructure не владеет recording truth.
- `composition.py` → service contracts + lazy concrete service/UI imports inside the builder only.
- `app/session_controller.py` → service contracts + stdlib only; no Tk/desktop adapters.
- `app/ports.py` → stdlib Protocols only.
- other `app/*` → core + owner modules + services/ui/application ports. Tk state остаётся у `VoiceFlowOfflineApp`; session identity/committed text — у controller.
- `entrypoint.py` → startup owners + lazy app import.
- `runtime.py` → package re-exports only. Ни функций, ни классов, ни mutable state внутри facade.

## Как выбирать owner при изменении

Hotkey parsing/VK → `hotkey_config.py`; physical press state → `core/hotkey_state.py`; dispatch/polling → `app/hotkeys.py`.

Recording state decision → `core/recording_state.py`; headless session transition/service coordination → `app/session_controller.py`; microphone side effects → `services/audio.py`; Tk lifecycle orchestration → `app/recording.py`.

Realtime text decision → `core/realtime.py`; committed session text → `app/session_controller.py`; inference → `services/transcription.py`; worker/UI/insertion flow → `app/streaming.py`.

Paste target/native Ctrl+V → `windows_insertion.py`; startup registry → `windows_startup.py`; user action/clipboard flow → `app/actions.py`.

Persisted schema/store → `settings.py`; widgets/editing controls → `app/ui.py` / `app/controls.py`.

## Mechanical guards

`tests/test_repository_contract.py` проверяет:

- отсутствие `context.py`;
- отсутствие wildcard imports во всём package;
- отсутствие внутренних imports из `runtime.py`;
- что `runtime.py` остаётся маленьким re-export-only facade;
- наличие canonical architecture/AI docs.

Если feature требует нарушить boundary, сначала пересмотри state owner и contract. Не обходи guard дополнительным facade, dynamic import или suppression без архитектурной причины.

## Service contracts

`services/contracts.py` is the behavioral port map for AudioRecorder, LocalTranscriber and LocalTextCleaner. Adapters such as `audio_devices.py` and `cuda_runtime.py` must remain independently testable with fakes. See SERVICE_CONTRACTS.md for expected call surfaces and test strategy.

## Composition guard

Concrete recorder/transcriber/cleaner/notification/tray construction belongs in `composition.py`. Keep those imports lazy so `ApplicationServices` and session tests remain importable before optional/package dependencies are installed.

Do not move Tk widgets into `session_controller.py`. Do not duplicate `session_id` or committed-text ownership back into mixins.

## Architecture 2.5 realtime boundaries

Canonical owners:
- audio facts / adaptive noisy-room pause metrics → `services/audio_analysis.py`;
- realtime timing / wait-vs-advance-vs-commit / final cancellation → `core/realtime_policy.py`;
- dedupe, punctuation, final-tail, voice-command text split → `core/realtime.py`;
- recognition worker and message production → `app/streaming.py`;
- queue message schema + stale/after-stop pure gates → `worker_messages.py`;
- main-thread queue decode and application/UI routing → `app/worker_dispatch.py`.

Dependency direction:
- `core/realtime_policy.py` → config constants + stdlib only;
- `services/audio_analysis.py` → stdlib at import time; production NumPy import is lazy;
- `worker_messages.py` → stdlib only;
- `app/streaming.py` may depend on the pure policy/message/service owners;
- `app/worker_dispatch.py` may call app/UI methods but must not become an inference/audio worker.

Do not place signal math back into `streaming.py`, do not place Tk/application effects into `worker_messages.py`, and do not bypass typed producers with new direct tuple queue writes.

## Architecture 2.6 worker-engine boundary

Canonical owner for the background frame loop is now `app/realtime_worker.py`.

Allowed direction:
- `app/streaming.py` → `app/realtime_worker.py` to build/configure/run the engine;
- `app/realtime_worker.py` → service contracts, session transcription DTO/port, audio analysis, pure realtime policy, voice-command text splitter, diagnostics and worker message contract;
- `app/realtime_worker.py` must not import Tkinter, pyautogui, tray/notification adapters or Windows insertion APIs.

Do not move `get_frames_since`, `SessionTranscriptionRequest`, frame-cursor retry rules or temporary-WAV cleanup back into `app/streaming.py`. Do not move UI/paste side effects into `RealtimeWorkerEngine`.

## Architecture 2.7 text-commit boundary

Canonical owners:
- primitive normalization/dedupe/punctuation/tail algorithms → `core/realtime.py`;
- LocalTextCleaner behavior → `services/text_cleaner.py` through `TextCleanerContract`;
- orchestration from worker text to display/command/exact insertion plan → `app/realtime_text_pipeline.py`;
- main-thread queue acceptance → `app/worker_dispatch.py`;
- Tk rendering / Windows paste / pyautogui command side effects → `app/streaming.py` and `app/actions.py`.

`app/realtime_text_pipeline.py` may depend on core text algorithms, `TextCleanerContract` and pure `voice_commands.py`. It must not import Tkinter, pyautogui, Windows insertion or runtime facade.

Do not make `worker_dispatch.py` manually rebuild punctuation/dedupe decisions. Do not make `streaming.py` choose raw-vs-cleaned or split trailing commands again. Preserve `commit_meta` through the planner.

## Architecture 2.8 delivery boundaries

Canonical owners:
- delivery Protocols/result DTO → `app/ports.py`;
- headless realtime delivery orchestration → `app/realtime_delivery.py`;
- concrete clipboard/current-target/pyautogui adapters → `desktop_delivery.py`;
- low-level HWND/native Ctrl+V helpers → `windows_insertion.py`;
- UI status/notifications after delivery → `app/streaming.py`.

Dependency direction:
- `app/realtime_delivery.py` → app ports + realtime insertion plan only;
- `app/streaming.py` → `RealtimeDeliveryController`, never pyautogui/windows insertion;
- `composition.py` wires concrete delivery adapters lazily;
- `desktop_delivery.py` may import optional pyperclip/pyautogui but must not import shared audio `dependencies.py`;
- Windows helper imports from `desktop_delivery.py` must remain function-local/lazy so offline delivery tests do not initialize the desktop/audio graph.

Do not bypass `TextInsertionPort` for realtime paste. Do not record session commits before delivery success.
