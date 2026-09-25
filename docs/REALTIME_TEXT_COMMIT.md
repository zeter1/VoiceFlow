# VoiceFlow — Realtime Text Commit & Exact Insertion Plan

Architecture 2.7 map for ChatGPT/Codex and developers. Read this before changing realtime cleanup, duplicate suppression, punctuation, raw-vs-cleaned insertion or trailing voice commands.

## Why this boundary exists

Recognition output is not yet the text that should be pasted.

Between Whisper and Windows insertion VoiceFlow must decide:
- whether the text is usable;
- how LocalTextCleaner should process it;
- whether an open/ellipsis chunk must lose temporary punctuation;
- whether a repeated prefix has already been inserted;
- whether a trailing phrase is a voice command rather than dictated text;
- raw vs cleaned insertion according to user setting;
- punctuation from real pause / Whisper sentence end / forced commit;
- continuation casing;
- the exact payload sent to the paste side effect.

Those are headless decisions. Tk, clipboard and Windows focus are side effects and must stay outside the planner.

## Owner: app/realtime_text_pipeline.py

`RealtimeTextPipeline` depends on:
- `TextCleanerContract`;
- pure algorithms from `core/realtime.py`;
- pure command parsing from `voice_commands.py`.

It imports no Tkinter, pyautogui or Windows insertion adapter.

### clean_for_commit()

Used by `RealtimeWorkerEngine` before it emits a stream result.

Contract:
1. normalize whitespace;
2. reject known bad/hallucination/punctuation-only raw chunks before cleaner;
3. call TextCleaner with `deep_grammar=False`;
4. reject unusable cleaned form or safely fall back to valid raw text;
5. remove temporary sentence-ending punctuation for ellipsis/open chunks.

### dedupe_chunk()

Uses the established core overlap algorithm while preserving intentional repeated exact voice-command phrases.

### plan_stream_result()

Main headless boundary after worker dispatch acceptance.

Inputs:
- raw / cleaned worker text;
- already successfully inserted text;
- origin;
- stream mode;
- raw-vs-cleaned user choice;
- full `commit_meta`.

Outputs `RealtimeResultPlan`:
- command-stripped raw text for display/logging;
- command-stripped cleaned text;
- optional command;
- whether command execution is permitted in current mode/origin;
- optional `RealtimeInsertionPlan`.

### RealtimeInsertionPlan

Contains:
- `text`: logical text that counts as successfully committed after paste;
- `paste_text`: exact external payload, including the existing trailing separator;
- `selected_source`: raw or cleaned;
- `punctuation`: complete `PasteChunkDecision` evidence.

The session controller must record `text`, not `paste_text`, and only after paste succeeds.

## commit_meta is behavioral input

Worker metadata includes fields such as:
- `pause_seconds`;
- `sentence_pause`;
- `forced_commit`;
- `whisper_sentence_end`;
- original raw text evidence.

This metadata changes punctuation and continuation behavior.

Architecture 2.7 fixed a real bug where `worker_dispatch.py` received `commit_meta` but called the old stream-text handler without it. The recognition layer therefore knew about a pause while the final paste decision could lose that fact.

Guard this path:

```text
StreamResultPayload.commit_meta
        ↓
WorkerDispatchMixin
        ↓
RealtimeTextPipeline.plan_stream_result(commit_meta=...)
        ↓
core.prepare_stream_chunk_for_paste()
        ↓
RealtimeInsertionPlan
```

## Voice-command separation

The planner strips a trailing command from both raw and cleaned text before insertion.

Example conceptually:

```text
"Привет отправь сообщение"
        ↓
display/insert text: "Привет"
command: Enter/send
```

Command execution is allowed only for the fragment-insertion hotkey path. Preview/main-window text must not trigger desktop commands.

The command is executed later by `app/streaming.py`; the headless planner never calls pyautogui or Windows APIs.

## Side-effect boundary

`app/worker_dispatch.py`:
1. validates current session;
2. builds `RealtimeResultPlan`;
3. passes planned display/insertion data to streaming side effects.

`app/realtime_delivery.py`:
- sends `insertion.paste_text` through `TextInsertionPort`;
- sends planned voice commands through `VoiceActionPort`;
- returns structured outcomes without Tk/Windows imports.

`app/streaming.py`:
- appends text to Tk widgets;
- logs the punctuation/delivery outcome;
- records `insertion.text` only after successful delivery;
- updates status/notifications.

It should not recalculate dedupe, command split or punctuation.

## Final-tail decision

`RealtimeTextPipeline.final_tail()` chooses raw or cleaned final text consistently with the user's insertion preference, then delegates suffix overlap detection to the pure core.

This keeps the final fallback path from using a different raw-vs-cleaned rule than realtime fragments.

## Offline regression matrix

`tests/test_realtime_text_pipeline.py` proves:
- whitespace cleanup and open ellipsis softening;
- bad raw rejection before cleaner;
- invalid cleaned fallback to valid raw;
- pause metadata produces the expected sentence punctuation;
- forced mid-sentence commit preserves continuation casing;
- raw vs cleaned choice is explicit;
- preview mode never plans a paste;
- trailing send command is removed from normal text and planned separately;
- inserted-text dedupe happens before punctuation;
- final-tail uses the selected text source.

`tests/test_repository_contract.py` additionally prevents decision wrappers from drifting back into `streaming.py` and requires `commit_meta` propagation in dispatch.

## What is still a runtime concern

Headless tests prove the planned text, not whether another application actually accepts it.

Still NOT VERIFIED without interactive Windows evidence:
- clipboard/native paste behavior in a specific target;
- foreground/focus races;
- privilege mismatch;
- pyautogui command execution;
- real Whisper punctuation quality and pause timing.

## Delivery port handoff

Architecture 2.8 moves the actual paste/action call behind `RealtimeDeliveryController`. `RealtimeTextPipeline` still owns **what** should happen; delivery ports own **performing** the external effect.

Read DELIVERY_PORTS.md when the planned text is correct but the desktop action is wrong.
