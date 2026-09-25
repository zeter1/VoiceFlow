# Архитектура VoiceFlow

VoiceFlow физически разделён по ответственности, чтобы сохранить realtime behavior и снизить blast radius изменений.

## Dependency map

voiceflow.py -> voiceflow_app.entrypoint -> app/main_window.py -> app mixins -> context.py -> runtime + services + ui infrastructure.

voiceflow.py является только launcher и не должен снова накапливать бизнес-логику.

## Runtime

voiceflow_app/runtime.py содержит пути logs/settings, structured diagnostics, dependency probing, single-instance lock, voice-command parsing, Windows startup/foreground/paste helpers, hotkey normalization/VK mapping и settings models/store.

При source-запуске APP_DIR остаётся корнем проекта. При frozen-запуске — каталогом VoiceFlow.exe.

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
app/hotkeys.py — registration, Windows polling, debounce and dispatch.
app/recording.py — idle repair, start/stop, runtime snapshot, warm-up.
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
Do not make context.py a dumping ground.
Prefer pure/testable helpers for parsing/dedupe/state decisions.
Before cross-module state changes identify owner, invariant, failure semantics and verification route.

See also: ../AGENTS.md, AI_CONTEXT.md, DEVELOPMENT.md, ../SECURITY.md.
