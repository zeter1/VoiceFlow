# VoiceFlow — Realtime Pipeline, Worker Engine & Queue Contracts

Карта для ChatGPT/Codex и разработчиков после Architecture 2.6. Открывай её перед изменениями пауз, noisy-room detection, frame cursor, realtime transcription context, worker cancellation, queue messages или session race behavior.

## Pipeline

```text
app/streaming.py
  builds RealtimeWorkerConfig + starts thread
                ↓
        RealtimeWorkerEngine
          ↙             ↘
services/audio_analysis  core/realtime_policy
          \             /
           audio facts + wait/advance/commit
                ↓
      AudioRecorder frame cursor
                ↓
 HeadlessSessionController.transcribe_frames()
                ↓
     temporary WAV → LocalTranscriber
                ↓
 cleanup/dedupe + raw/clean worker context
                ↓
        typed WorkerMessage
                ↓
       app/worker_dispatch.py
                ↓
 stale/after-stop gate + main-thread application
                ↓
       UI / Windows insertion
```

Authoritative recording `session_id` and committed **inserted** text remain owned by `HeadlessSessionController`. The worker engine owns only its per-worker frame cursor and transcription prompt contexts.

## Owner: services/audio_analysis.py

Input: captured frame arrays + sample rate.

Output facts:
- duration;
- RMS / peak / peak-to-RMS;
- active sample ratio;
- raw trailing silence;
- adaptive speech trailing silence;
- combined pause seconds;
- estimated noise and speech thresholds.

Noisy-room logic uses short RMS windows so constant fan/PC noise is less likely to erase real pauses.

Import-time contract: NumPy is lazy. Pure policy/engine tests can import without initializing PortAudio/audio runtime.

## Owner: core/realtime_policy.py

Input: audio facts + realtime profile.

Output: `ChunkCommitDecision`.

Cursor meaning of the decision:
- below minimum duration → `commit=False, advance_frame=False`: keep accumulating;
- enough duration but not probable speech → advance past noise;
- pause detected → commit;
- max duration reached → forced commit;
- final short/quiet chunk → skip/advance;
- stop with `STREAM_FINAL_CHUNK_ON_STOP=False` → skip final inference.

Timing thresholds must not be copied into streaming or worker-dispatch modules.

## Owner: app/realtime_worker.py

`RealtimeWorkerEngine` is the headless background-loop owner.

Responsibilities:
- keep `last_frame_index`;
- read new recorder frames;
- call audio statistics and commit policy once per candidate;
- build `SessionTranscriptionRequest`;
- call the session transcription port;
- keep raw/clean transcription prompt context;
- filter bad/empty chunks through injected cleanup;
- dedupe through injected text policy;
- exclude trailing voice commands from prompt context;
- build commit metadata;
- publish `StreamResultPayload` / `StreamWarningPayload`;
- clean temporary WAV paths;
- handle worker cancellation/final-chunk policy.

The engine imports no Tkinter and no Windows insertion/UI adapter.

### Frame cursor contract

This is a critical regression boundary.

```text
too short                     → KEEP cursor
noise / not probable speech   → ADVANCE cursor
filtered hallucination/empty  → ADVANCE cursor
valid or deduped transcription→ ADVANCE cursor
transcription exception       → KEEP cursor, emit warning, retry overlap
```

The old filtered-chunk bug came from retaining a bad/growing chunk indefinitely. Do not reintroduce retry retention for known filtered/noise text without a new explicit bounded design and regression proof.

### Error and WAV cleanup contract

`wav_path` starts as `None`. Cleanup happens only after a `SessionTranscript` has supplied a path.

Why: `transcribe_frames()` can throw before returning a transcript. Cleanup must preserve that original exception so logs and `StreamWarningPayload.error` report the real failure, not an `UnboundLocalError` from cleanup.

### Context reset

Voice commands can request message-context reset through `stream_context_reset_event`. On reset the worker clears raw and cleaned prompt contexts before the next transcription request. Recording session id does not change.

## Owner: app/streaming.py

After 2.6 this is **not** the frame-loop owner.

Responsibilities:
- choose preview vs fragment mode;
- choose realtime model/quality using current application policy;
- create/start/stop/finalize the worker thread;
- build `RealtimeWorkerConfig`;
- provide existing cleanup/dedupe callbacks to the engine;
- maintain UI-side stream text shaping;
- apply punctuation-for-paste decisions;
- execute insertion and voice-command effects.

If a change needs `get_frames_since`, transcription request context or frame cursor, it belongs in `realtime_worker.py`, not here.

## Owner: worker_messages.py

Background → main-thread schema:
- `WorkerMessageKind`;
- `WorkerMessage`;
- typed payload dataclasses;
- `put_worker_message()`;
- pure stale/after-stop session gates.

`coerce_worker_message()` accepts legacy two-item tuples only as a migration seam. New producers should remain typed.

## Owner: app/worker_dispatch.py

Main-thread consumer:
- decode worker message;
- reject stale session results;
- reject late result after immediate stop;
- update Tk/application state;
- route hotkey/tray/external actions;
- finish recording finalization;
- drain/poll queue.

It must not perform audio analysis or Whisper inference.

## Race contracts

### Old worker after restart

Session-7 worker may finish physically after session 8 starts. Its message must be rejected by current-session gate.

### Immediate stop

A current-session realtime result arriving after recorder stop is ignored unless the app is in explicit finalization.

### Stop ordering

Keep:
`stop capture keeping frames → signal worker → discard buffered frames`.

### Cancellation responsiveness

Production currently uses `STREAM_FINAL_CHUNK_ON_STOP=False`, so stop prioritizes repeat-hotkey responsiveness over an extra final inference.

## Offline proof

- `tests/test_realtime_audio.py` — signal facts/noisy-room pause;
- `tests/test_realtime_policy.py` — timing and wait/advance/commit/cancel;
- `tests/test_realtime_worker.py` — speech→result, noise→continue, filtered recovery, transcription-error retry, stop cancellation and context reset;
- `tests/test_worker_messages.py` — typed queue and stale/after-stop gates;
- `tests/test_realtime_core.py` — dedupe/punctuation/final-tail;
- `tests/test_session_controller.py` — session lifecycle/recovery/stop ordering;
- `tests/test_repository_contract.py` — module size/headless/import ownership guards.

## Runtime evidence still required

Automated tests and packaged self-test do **not** prove:
- real microphone/noise thresholds;
- real Whisper latency/accuracy;
- CUDA behavior on a specific GPU;
- hotkey timing under desktop load;
- tray callbacks;
- insertion into Chrome/Firefox/Telegram/elevated or unusual Windows applications.

Mark those claims `NOT VERIFIED` until interactive Windows evidence exists.
