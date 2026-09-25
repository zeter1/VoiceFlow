# VoiceFlow — Application Composition & Headless Session

Карта для ChatGPT/Codex и разработчиков после Architecture 2.4.

## Ownership

`composition.py` — default concrete-construction boundary.

`ApplicationServices` содержит:
- `AudioRecorderContract`;
- `TranscriberContract`;
- `TextCleanerContract`;
- notification factory;
- tray factory.

`VoiceFlowOfflineApp` — Tk shell. Он получает services через constructor injection и не должен снова создавать concrete recorder/transcriber/cleaner/notification/tray напрямую.

`HeadlessSessionController` — authoritative owner:
- session_id;
- phase `idle → starting → recording → finalizing → idle`;
- committed realtime text;
- inserted_any;
- recorder lifecycle/recovery;
- frames → WAV → transcription → optional cleanup coordination.

## What stays outside the controller

Tk button/status/timer variables, notification layout, tray menu, global hotkey polling, current Windows paste target, clipboard/native insertion and pure punctuation/dedupe policy.

Это специально: controller должен импортироваться и тестироваться без GUI, microphone, Whisper, CUDA и Windows desktop.

## Production start path

1. `RecordingMixin.start_recording` validates selected microphone/settings.
2. `session_controller.begin_capture()` reserves a new monotonically increasing session id and enters STARTING.
3. Tk/application setup captures target and clears previous UI text.
4. `session_controller.activate_capture()` starts AudioRecorder and enters RECORDING.
5. Streaming worker starts with that session id.

If start fails, `abort_start()/recover_idle()` restore the headless phase before the next attempt.

## Realtime fragment path

1. Streaming worker collects stable frames.
2. `session_controller.transcribe_frames(...)` coordinates recorder frames_to_wav and TranscriberContract.
3. Streaming layer applies realtime-specific bad-text/punctuation/voice-command policy.
4. Paste succeeds.
5. `session_controller.record_commit(text)` updates authoritative committed text.
6. Dedup/final-tail decisions read the controller-backed committed state.

## Stop-order invariant

Do not casually reorder:

`stop capture keeping frames → signal streaming stop-event → discard buffered frames → release Tk state`.

The explicit deferred-discard regression test exists because clearing frames before signalling the worker can create a stop race and lose the last available chunk.

## Headless tests

`tests/test_session_controller.py` exercises without Tk or hardware:
- start → frames → transcription → cleanup → stable commit → stop;
- duplicate/double start rejection;
- stop then restart and new session id;
- committed text reset on restart;
- failed start recovery;
- failed stop recovery;
- finalizing/restart race;
- deferred discard ordering.

`tests/test_composition.py` proves ApplicationServices can be built entirely from fakes.

`tests/test_repository_contract.py` ensures composition defaults stay lazy and recording uses the public transcriber backend contract.

## How to change lifecycle code

1. Identify whether the change is a pure decision, headless session transition, hardware service effect or Tk presentation.
2. Put it in the closest owner.
3. Add/adjust a headless regression test first when possible.
4. Preserve session id and stop-order invariants.
5. Run compile + full offline unittest suite.
6. For package-affecting changes run PyInstaller + packaged `--self-test`.
7. Real microphone/hotkey/CUDA/paste claims remain NOT VERIFIED until interactive Windows evidence exists.

## Realtime worker/message integration

The session controller remains authoritative for `session_id` and committed text. Architecture 2.5 adds a separate message boundary rather than moving that ownership.

Background realtime work produces `WorkerMessage` values. `worker_messages.classify_stream_result()` compares payload session id with the current controller session before `WorkerDispatchMixin` is allowed to update widgets or insertion state. A result from an old session is discarded; a current result arriving after immediate stop is also discarded unless the app is explicitly finalizing.

This separation is important for stop→restart races: the old worker may physically finish later, but it must not mutate the new session.
