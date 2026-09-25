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
| application/session orchestration | `app/*` |

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
- `app/*` → core + owner modules + services/ui. Mutable session state остаётся у `VoiceFlowOfflineApp`.
- `entrypoint.py` → startup owners + lazy app import.
- `runtime.py` → package re-exports only. Ни функций, ни классов, ни mutable state внутри facade.

## Как выбирать owner при изменении

Hotkey parsing/VK → `hotkey_config.py`; physical press state → `core/hotkey_state.py`; dispatch/polling → `app/hotkeys.py`.

Recording state decision → `core/recording_state.py`; microphone side effects → `services/audio.py`; lifecycle orchestration → `app/recording.py`.

Realtime text decision → `core/realtime.py`; inference → `services/transcription.py`; worker/session flow → `app/streaming.py`.

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
