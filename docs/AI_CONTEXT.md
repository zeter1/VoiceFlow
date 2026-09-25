# VoiceFlow — карта проекта для ChatGPT/Codex

Сначала прочитай ../AGENTS.md, затем только релевантный раздел этого файла и owner-модуль. Не перечитывай весь проект без причины.

## Pipeline

microphone -> AudioRecorder -> realtime buffer -> LocalTranscriber -> stability/dedupe -> voice command/cleanup -> Windows insertion -> active application.

## Физическая карта

- runtime.py: compatibility facade only; do not add new implementation.
- config.py: paths and runtime constants.
- diagnostics.py: logs, diagnostics and exception hooks.
- dependencies.py: third-party modules and microphone discovery.
- hotkey_config.py: shortcut normalization/VK mapping.
- settings.py: persisted and runtime settings.
- voice_commands.py: voice command vocabulary/parsing.
- windows.py: Windows startup/target/native paste adapters.
- core/realtime.py: pure realtime text decisions.
- core/recording_state.py: pure recording state/repair classification.
- core/hotkey_state.py: pure hotkey edge/debounce decisions.
- services/audio.py: microphone capture.
- services/transcription.py: model lifecycle, CPU/CUDA, inference.
- services/text_cleaner.py: punctuation/fillers/grammar/formatting.
- app/ui.py: Tk interface.
- app/controls.py: microphone/hotkey controls.
- app/hotkeys.py: registration/polling/debounce/dispatch.
- app/recording.py: recording state transitions and warm-up.
- app/streaming.py: realtime worker/queue/orchestration; dedupe/punctuation/tail decisions delegate to core/realtime.py.
- app/actions.py: copy/paste/settings/shutdown.
- ui/notifications.py and ui/tray.py: UI infrastructure.
- entrypoint.py: startup + packaged self-test.

## State owners

VoiceFlowOfflineApp owns GUI/session orchestration. SettingsStore owns persistence. AudioRecorder owns capture stream/frames. LocalTranscriber owns model/inference state. Notification/tray managers do not own recording truth.

When adding a persisted setting: AppSettings -> RuntimeSettings if worker needs it -> load/save/UI -> docs -> regression contract where possible.

## Thread boundary

Tk main thread handles widgets. Capture/inference use background execution. Worker -> UI/app communication goes through queue/scheduled callbacks. Do not fix a race with arbitrary sleep before understanding the state transition.

## Realtime contract

Confirmed fragments are inserted during recording. app/streaming.py tracks committed text, rejects bad/duplicate chunks and handles sentence tails. Stop must release session state without pasting the full transcript again.

## Hotkey diagnostics

Symptom "works once", double start/stop or stuck hotkey: inspect app/hotkeys.py, hotkey_trace.jsonl and recording_state.jsonl first.

## Insertion diagnostics

For wrong target/paste failure: runtime Windows target helpers + app/actions.py + insertion.jsonl. Check foreground restoration, clipboard/native Ctrl+V fallback and privilege mismatch.

## Logs

Per run: voiceflow_logs/run_YYYY-MM-DD_HH-MM-SS_PID.
Latest mirror: voiceflow_logs/_last_run.

voiceflow.txt = general lifecycle/errors.
diagnostics.json = environment/dependencies.
hotkeys.jsonl/hotkey_trace.jsonl = hotkey evidence.
recording_state.jsonl = recording transitions.
streaming.jsonl = realtime recognition.
worker_queue.jsonl = worker messages/backlog.
insertion.jsonl = paste path.
notifications.jsonl = notification lifecycle.
dictation_text.txt = private dictated text; review before sharing.

## Build/release

Normal source push: compile + offline unittest.
[package] push/workflow_dispatch: install deps -> PyInstaller onedir -> VoiceFlow.exe --self-test -> ZIP/SHA-256 -> prerelease.

Green package CI does NOT prove a real microphone, a downloaded Whisper model, user CUDA, global desktop hotkeys, tray behavior or insertion into arbitrary third-party windows. Mark those NOT VERIFIED without runtime evidence.

## AI completion handoff

Return: source commit -> actual delta -> checks PASS/FAIL -> NOT VERIFIED -> residual risk/next engineering stage.

## Educational documentation

User-facing guides are isolated under user-guide/. Technical/AI work should start from AGENTS.md, ARCHITECTURE.md, AI_CONTEXT.md and DEVELOPMENT.md rather than scanning tutorial files.

Architecture 2.1 rule: app/* and entrypoint.py use explicit owner imports. context.py no longer exists. runtime.py is a compatibility surface, not an implementation owner.
