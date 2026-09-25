# Архитектура VoiceFlow

VoiceFlow физически разделён по ответственности, чтобы сохранить realtime behavior и снизить blast radius изменений.

## Dependency map

voiceflow.py -> voiceflow_app.entrypoint -> app/main_window.py -> app mixins -> focused owners (core/config/diagnostics/dependencies/hotkey_config/settings/voice_commands/windows/services/ui).

voiceflow.py является только launcher и не должен снова накапливать бизнес-логику.

## Runtime decomposition

Architecture 2.1 убирает giant runtime owner:

- config.py — APP_DIR, log/settings paths и стабильные runtime constants;
- diagnostics.py — structured logs, dependency snapshot, single-instance lock и exception hooks;
- dependencies.py — numpy/sounddevice и optional third-party modules + microphone discovery;
- hotkey_config.py — normalization, validation и Windows VK mapping;
- settings.py — AppSettings, RuntimeSettings и SettingsStore;
- voice_commands.py — vocabulary и text-to-command parsing;
- windows.py — startup, HWND/focus target и native Ctrl+V;
- runtime.py — только внешний compatibility facade с re-export owner symbols; внутренний код его не импортирует.

При source-запуске APP_DIR остаётся корнем проекта. При frozen-запуске — каталогом VoiceFlow.exe.

## Pure core

voiceflow_app/core/realtime.py содержит deterministic решения dedupe, bad-chunk rejection, punctuation, continuation casing, final-tail и trailing voice-command split. Он не импортирует Tkinter, device/Whisper adapters или runtime facade.

voiceflow_app/core/recording_state.py классифицирует recording snapshot и решает, когда idle state является stale/recoverable.

voiceflow_app/core/hotkey_state.py хранит edge state machine физического нажатия/release и action decision для debounce/start/stop/finalizing.

Эти модули являются preferred unit-test seam: сначала меняй decision + regression test, затем orchestration.

## Services

services/audio.py — microphone capture и frame lifecycle.
services/transcription.py — lazy faster-whisper model, CPU/CUDA/compute policy, warm-up/inference.
services/text_cleaner.py — punctuation, fillers, typo repair, sentence flow, term preservation, optional LanguageTool and output modes.

Audio callback не выполняет Whisper inference. CUDA/model/audio failures диагностируются отдельно.

## UI infrastructure

ui/notifications.py и ui/tray.py отвечают только за presentation/lifecycle своих UI components и не являются источником истины о recording state.

## Application composition

app/main_window.py собирает VoiceFlowOfflineApp.
app/ui.py — widgets/state/notifications.
app/controls.py — microphone/hotkey editor.
app/hotkeys.py — Windows polling/dispatch; edge/debounce decisions делегируются core/hotkey_state.py.
app/recording.py — side effects start/stop/warm-up; state classification делегируется core/recording_state.py.
app/streaming.py — chunk scheduling, speech stats, dedupe, realtime worker, queue and voice commands.
app/actions.py — copy/paste, settings, window lifecycle and shutdown.

## Runtime data flow

AudioRecorder -> frames -> StreamingMixin worker -> LocalTranscriber -> stable fragment filters -> optional LocalTextCleaner/voice command -> ActionsMixin insertion -> active Windows target.

## State/thread invariants

Conceptual state: idle -> starting -> recording -> stopping/finalizing -> idle.
Hotkey only initiates transitions; heavy work stays out of callback.
Tk updates occur through Tk/main-thread scheduling.
Realtime confirmed fragments are inserted while recording; stop must not reinsert the complete transcript.

## Settings/privacy

AppSettings = persisted user configuration.
RuntimeSettings = processing snapshot.
SettingsStore = persistence owner.

voiceflow_settings and voiceflow_logs are runtime/private data and stay outside Git.

## Verification boundary

Offline CI: parse/compile + repository contracts.
Package CI: dependencies + PyInstaller + packaged --self-test + ZIP/checksum/release.
Real microphone, desktop hotkeys, tray, arbitrary target apps and user CUDA require Windows runtime evidence.

## Architecture rules

Do not rebuild a giant voiceflow.py.
Keep new behavior in the closest owner module.
Internal modules must not import runtime.py; import the focused owner directly.
Prefer pure/testable helpers for parsing/dedupe/state decisions.
Before cross-module state changes identify owner, invariant, failure semantics and verification route.

See also: ../AGENTS.md, AI_CONTEXT.md, IMPORT_BOUNDARIES.md, DEVELOPMENT.md, ../SECURITY.md.

## Compatibility boundary

context.py удалён. Architecture 2.2 перевела app/*, services/*, ui/* и entrypoint.py на явные owner imports.

runtime.py не имеет внутренних consumers и сохраняется только как внешний compatibility import path. Он состоит только из package re-exports; repository contracts запрещают добавлять в него implementation или возвращать внутренние зависимости на facade.

Подробные разрешённые направления зависимостей: IMPORT_BOUNDARIES.md.
