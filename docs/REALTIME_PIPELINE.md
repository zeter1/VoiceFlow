# VoiceFlow — Realtime Pipeline & Queue Contracts

Карта для ChatGPT/Codex и разработчиков после Architecture 2.5. Используй её перед изменениями пауз, шумоподавления, realtime chunk commit, worker queue или session race behavior.

## Pipeline

```text
AudioRecorder frames
      ↓
services/audio_analysis.py
      ↓  signal facts
core/realtime_policy.py
      ↓  wait / advance / commit
app/streaming.py
      ↓  WAV → transcription → cleanup/dedupe
WorkerMessage
      ↓
app/worker_dispatch.py
      ↓  stale/after-stop gate + main-thread application
UI / Windows insertion
```

Session identity and committed inserted text are still owned by `HeadlessSessionController`.

## Owner: services/audio_analysis.py

Inputs: captured frame arrays + sample rate.

Outputs are facts, not product decisions:
- duration;
- RMS and peak;
- peak/RMS ratio;
- active sample ratio;
- raw trailing silence;
- adaptive speech trailing silence;
- combined pause seconds;
- estimated noise/speech thresholds.

Noisy-room logic uses short RMS windows so a constant fan/PC background does not always look like continuous speech.

Import-time rule: NumPy is not eagerly imported by this module. Pure threshold/stat-summary tests must work without initializing audio dependencies.

## Owner: core/realtime_policy.py

Input: signal facts + configured speed profile + CPU/GPU path expectations.

Output: `ChunkCommitDecision`:
- `commit`;
- `advance_frame`;
- pause/sentence-pause flags;
- forced max-duration commit;
- reason.

Important semantics:
- below minimum duration → wait, do not advance;
- long enough but clearly not speech → skip and advance;
- enough pause → commit;
- no pause but max duration reached → forced commit;
- final short/quiet chunk → skip;
- stop with `STREAM_FINAL_CHUNK_ON_STOP=False` → do not run final recognition, keeping repeat-hotkey responsive.

Do not duplicate these thresholds inside `app/streaming.py`.

## Owner: app/streaming.py

Responsibilities:
- start/stop realtime worker thread;
- read frames from recorder;
- obtain audio facts and policy decision;
- call `HeadlessSessionController.transcribe_frames()`;
- filter hallucinated/bad text;
- dedupe raw/clean context;
- build commit metadata;
- publish typed worker messages;
- execute realtime text/voice-command insertion helpers inherited by the app.

It does **not** own main-thread queue polling/dispatch anymore.

## Owner: worker_messages.py

This is the background→main-thread contract.

Use:
- `WorkerMessageKind`;
- payload dataclasses such as `StreamResultPayload`, `StreamWarningPayload`, `HotkeyPressedPayload`, `ExternalTogglePayload`;
- `put_worker_message()`.

`coerce_worker_message()` accepts old two-item tuples only as a bounded migration seam. Do not use that compatibility as a reason to create new tuple producers.

Pure gates:
- `classify_stream_result()`;
- `is_current_session()`.

## Owner: app/worker_dispatch.py

Runs on the Tk/main-thread side and translates typed messages to application effects:
- hotkey dispatch;
- tray/external actions;
- stream result application;
- warning/finalization handling;
- stale-session suppression;
- after-stop suppression;
- queue draining/polling;
- recording-state watchdog.

It may invoke UI/application methods, but must not become a transcription/audio worker.

## Race contracts

### Old worker after restart

If session 7 stops and session 8 starts before an old session-7 message is dequeued, session-7 result must be ignored.

### Result after immediate stop

A current-session stream result arriving after recorder stop and outside finalization must not be pasted or shown as a new live fragment.

### Final cancellation

Current product behavior prioritizes immediate hotkey reuse: stop normally skips final chunk recognition when `STREAM_FINAL_CHUNK_ON_STOP=False`.

### Stop ordering

Keep the Architecture 2.4 invariant:
`stop capture keeping frames → signal worker → discard frames`.

## Offline proof

Primary tests:
- `tests/test_realtime_audio.py` — thresholds and noisy-room pause facts;
- `tests/test_realtime_policy.py` — timing profiles, wait/advance/commit, forced commit and final cancellation;
- `tests/test_worker_messages.py` — typed message round-trip, migration coercion, stale/after-stop gates;
- `tests/test_realtime_core.py` — dedupe/punctuation/final-tail decisions;
- `tests/test_session_controller.py` — lifecycle/session/restart/recovery;
- `tests/test_repository_contract.py` — module size/import/message-producer architecture guards.

## Runtime evidence still required

Offline/packaged checks do not prove:
- real microphone/noise behavior;
- real Whisper latency/accuracy;
- CUDA behavior on the user's GPU;
- global hotkey timing under desktop load;
- tray callback timing;
- insertion into Chrome/Firefox/Telegram/other elevated or unusual windows.

Mark those claims `NOT VERIFIED` until interactive Windows evidence exists.
