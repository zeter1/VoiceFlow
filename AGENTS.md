# AGENTS.md — VoiceFlow repository operating guide

Durable repository context for ChatGPT, Codex and other coding agents. One-off acceptance criteria belong in the current task.

## Mission and source of truth

VoiceFlow is a Windows-first offline dictation application: microphone -> faster-whisper -> stable realtime text -> optional voice command/cleanup -> current Windows input target.

Source of truth order: current main/files -> failing test/Actions/runtime logs -> this file + docs/ARCHITECTURE.md -> older prose docs. Preserve working behavior unless a task explicitly changes it.

## Fast map

- voiceflow.py — thin compatibility launcher only.
- voiceflow_app/composition.py — default/injectable ApplicationServices composition root.
- voiceflow_app/runtime.py — small backward-compatible facade only; new internal code must not put implementation here.
- voiceflow_app/core/realtime.py — pure dedupe/punctuation/final-tail/voice-command decisions.
- voiceflow_app/core/realtime_policy.py — pure timing/profile/chunk-commit/final-cancellation policy.
- voiceflow_app/core/recording_state.py — pure recording-state classification.
- voiceflow_app/core/hotkey_state.py — pure hotkey edge/debounce decisions.
- voiceflow_app/config.py — paths and stable runtime constants.
- voiceflow_app/diagnostics.py — logging, diagnostic snapshots and process-level failure handling.
- voiceflow_app/dependencies.py — third-party package loading only.
- voiceflow_app/audio_devices.py — microphone/input-device discovery adapter.
- voiceflow_app/cuda_runtime.py — CUDA runtime/DLL/preflight adapter and backend policy.
- voiceflow_app/windows_startup.py — Windows startup-registry adapter.
- voiceflow_app/windows_insertion.py — foreground target and native paste adapter.
- voiceflow_app/hotkey_config.py — hotkey normalization/VK mapping.
- voiceflow_app/settings.py — AppSettings/RuntimeSettings/SettingsStore.
- voiceflow_app/voice_commands.py — command vocabulary and parsing.
- voiceflow_app/windows.py — startup, foreground target and native paste adapters.
- services/audio.py — AudioRecorder.
- services/audio_analysis.py — realtime audio statistics; NumPy loaded lazily for production frames.
- services/transcription.py — LocalTranscriber / faster-whisper; environment probing is injected.
- services/text_cleaner.py — LocalTextCleaner.
- services/contracts.py — explicit Protocol contracts for recorder/transcriber/cleaner.
- ui/notifications.py — notification windows.
- ui/tray.py — system tray.
- app/main_window.py — Tk shell/composition consumer; concrete services are injected.
- app/ports.py — Notification/Tray application-facing Protocol ports.
- app/session_controller.py — headless session_id/capture/committed-text/service-pipeline owner.
- app/ui.py — widgets/state snapshots/notifications.
- app/controls.py — microphone + hotkey editor controls.
- app/hotkeys.py — global hotkey polling/dispatch.
- app/recording.py — start/stop/warm-up.
- app/streaming.py — realtime producer/recognition/insertion orchestration; no main-thread queue dispatch.
- app/worker_dispatch.py — typed worker queue decoding, stale-session gates and main-thread UI/application routing.
- worker_messages.py — typed queue message kinds/payloads and pure session-result gates.
- app/actions.py — copy/paste/settings/window lifecycle/shutdown.
- tests/test_session_controller.py — headless lifecycle/race/recovery integration tests.
- tests/test_composition.py — composition injection contract.
- tests/test_repository_contract.py — offline repository contracts.
- .github/workflows/python-check.yml — validation, package, self-test, release.
- docs/AI_CONTEXT.md — task-to-file navigation.
- docs/DEVELOPMENT.md — verification/release commands.
- docs/IMPORT_BOUNDARIES.md — canonical import owners and dependency directions.
- docs/SERVICE_CONTRACTS.md — service ports, adapters, injection seams and offline-test strategy.
- docs/REALTIME_PIPELINE.md — realtime audio→policy→worker-message→dispatch ownership and race contracts.

## Critical invariants

1. Source logs/settings remain beside the repository entrypoint; frozen logs/settings remain beside VoiceFlow.exe.
2. Stable fragments are inserted during recording; stop must not paste the whole transcript again.
2a. `HeadlessSessionController` owns session identity and committed realtime text; Tk mixins must not create a second competing owner.
3. Hotkey/audio callbacks must not perform heavy inference/UI work inline.
4. Tk widgets are updated through Tk/main-thread scheduling, not arbitrary worker threads.
5. Active paste target semantics must remain compatible while the user changes windows during dictation.
6. Settings/logs remain ignored by Git and are private runtime data.
7. CPU path must remain viable without NVIDIA; classify CUDA failures separately.
8. voiceflow.py stays thin. Do not rebuild a monolith there.
9. runtime.py is an external compatibility facade only. No internal module may depend on it.
10. New logs must not intentionally expose secrets; dictated text is private data.

## Task routing

Microphone capture -> services/audio.py + app/session_controller.py + app/recording.py. Microphone enumeration -> audio_devices.py + app/controls.py.
Whisper/model -> services/transcription.py + app/streaming.py. CUDA/DLL/preflight -> cuda_runtime.py.
Duplicates/missing realtime text -> core/realtime.py + app/session_controller.py committed state + app/streaming.py.
Pause/noise/forced-commit behavior -> services/audio_analysis.py + core/realtime_policy.py.
Stale worker result / queue race -> worker_messages.py + app/worker_dispatch.py + worker_queue.jsonl.
Punctuation/cleanup -> services/text_cleaner.py.
Voice commands -> voice_commands.py parsing + app/streaming.py execution.
Hotkey starts once/double fires -> app/hotkeys.py + hotkey_trace.jsonl.
Wrong-window/paste failure -> windows_insertion.py + app/actions.py + insertion.jsonl.
Tray/notification -> ui/*.
Settings/autostart -> settings.py + windows_startup.py + app/actions.py.
EXE/release -> workflow + RELEASE_NOTES_RU.md.

## Change workflow

INSPECT -> DIAGNOSE -> PLAN -> CHANGE -> VERIFY -> REVIEW -> DELIVER.

For code changes update CHANGELOG.md. Use [package] only when an updated Windows artifact is intended; update RELEASE_NOTES_RU.md in that same logical commit.

Prefer minimal diffs, explicit state ownership, queue/message contracts and regression tests for stable offline behavior. Do not hide failures with continue-on-error, disabled tests or broad try/except.

## Verification ladder

1. Static parse/compile.
2. Offline unittest contracts.
3. PyInstaller + packaged VoiceFlow.exe --self-test.
4. Interactive Windows GUI/hotkey/microphone/paste.
5. Real CPU/CUDA model/audio-device scenario.

CI package runs prove 1-3, not 4-5. Mark unsupported claims NOT VERIFIED.

## Definition of done

Know current source commit, review actual diff, pass relevant checks, inspect final Actions, verify package/release when requested, and explicitly name any unverified Windows/hardware behavior.

## Compatibility migration status

Architecture 2.2 removes every internal dependency on runtime.py and every wildcard import in voiceflow_app. runtime.py is external compatibility only and may re-export owner symbols, but it must contain no implementation. See docs/IMPORT_BOUNDARIES.md.

## Documentation split

Technical/AI docs stay in docs/. End-user educational material lives in docs/user-guide/. Do not mix user tutorials back into the technical architecture root.

## Architecture 2.3 service boundary

Application orchestration depends on service behavior, not CUDA/DLL/PortAudio/registry discovery details. Keep environment probing in adapters. When changing AudioRecorder, LocalTranscriber or LocalTextCleaner, check services/contracts.py and docs/SERVICE_CONTRACTS.md first. Prefer a fake-backed offline test before hardware/runtime validation.

## Architecture 2.4 application/session boundary

`composition.py` is the concrete-service creation boundary. `VoiceFlowOfflineApp` may accept injected `ApplicationServices` and should not instantiate recorder/transcriber/cleaner/notification/tray implementations directly.

`app/session_controller.py` is stdlib + service-contract based and is the preferred seam for capture lifecycle/session identity/committed realtime text and frames→WAV→transcription orchestration. Preserve the stop ordering invariant: stop capture → signal realtime worker → discard buffered frames.

Before changing start/stop/restart/finalizing behavior, run/read `tests/test_session_controller.py` and `docs/APPLICATION_SESSION.md`.

## Architecture 2.5 realtime boundary

Realtime ownership is deliberately split: `services/audio_analysis.py` measures audio, `core/realtime_policy.py` decides whether a candidate should wait/advance/commit, `app/streaming.py` performs recognition and emits typed messages, and `app/worker_dispatch.py` applies messages on the main thread.

All new producer messages should use `put_worker_message(...)` with `WorkerMessageKind`; do not reintroduce `worker_queue.put(("string", payload))`. Stale/after-stop result acceptance belongs in `worker_messages.py`, not scattered UI branches.

Before changing pause/noise/cancellation/session-result behavior, read `docs/REALTIME_PIPELINE.md` and run `test_realtime_audio.py`, `test_realtime_policy.py`, `test_worker_messages.py` plus the existing realtime/session suite.
