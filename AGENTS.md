# AGENTS.md — VoiceFlow repository operating guide

Durable repository context for ChatGPT, Codex and other coding agents. One-off acceptance criteria belong in the current task.

## Mission and source of truth

VoiceFlow is a Windows-first offline dictation application: microphone -> faster-whisper -> stable realtime text -> optional voice command/cleanup -> current Windows input target.

Source of truth order: current main/files -> failing test/Actions/runtime logs -> this file + docs/ARCHITECTURE.md -> older prose docs. Preserve working behavior unless a task explicitly changes it.

## Fast map

- voiceflow.py — thin compatibility launcher only.
- voiceflow_app/runtime.py — constants, paths, diagnostics, settings, Windows helpers, hotkey/voice-command parsing.
- services/audio.py — AudioRecorder.
- services/transcription.py — LocalTranscriber / faster-whisper.
- services/text_cleaner.py — LocalTextCleaner.
- ui/notifications.py — notification windows.
- ui/tray.py — system tray.
- app/main_window.py — VoiceFlowOfflineApp composition/constructor.
- app/ui.py — widgets/state snapshots/notifications.
- app/controls.py — microphone + hotkey editor controls.
- app/hotkeys.py — global hotkey polling/dispatch.
- app/recording.py — start/stop/warm-up.
- app/streaming.py — chunking, dedupe, realtime worker, queue, voice commands.
- app/actions.py — copy/paste/settings/window lifecycle/shutdown.
- tests/test_repository_contract.py — offline repository contracts.
- .github/workflows/python-check.yml — validation, package, self-test, release.
- docs/AI_CONTEXT.md — task-to-file navigation.
- docs/DEVELOPMENT.md — verification/release commands.

## Critical invariants

1. Source logs/settings remain beside the repository entrypoint; frozen logs/settings remain beside VoiceFlow.exe.
2. Stable fragments are inserted during recording; stop must not paste the whole transcript again.
3. Hotkey/audio callbacks must not perform heavy inference/UI work inline.
4. Tk widgets are updated through Tk/main-thread scheduling, not arbitrary worker threads.
5. Active paste target semantics must remain compatible while the user changes windows during dictation.
6. Settings/logs remain ignored by Git and are private runtime data.
7. CPU path must remain viable without NVIDIA; classify CUDA failures separately.
8. voiceflow.py stays thin. Do not rebuild a monolith there.
9. context.py is a transitional compatibility namespace, not misc.py.
10. New logs must not intentionally expose secrets; dictated text is private data.

## Task routing

Microphone -> services/audio.py + app/recording.py.
Whisper/CUDA/model -> services/transcription.py + app/streaming.py.
Duplicates/missing realtime text -> app/streaming.py.
Punctuation/cleanup -> services/text_cleaner.py.
Voice commands -> runtime.py parsing + app/streaming.py execution.
Hotkey starts once/double fires -> app/hotkeys.py + hotkey_trace.jsonl.
Wrong-window/paste failure -> runtime.py target helpers + app/actions.py + insertion.jsonl.
Tray/notification -> ui/*.
Settings/autostart -> runtime.py + app/actions.py.
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
