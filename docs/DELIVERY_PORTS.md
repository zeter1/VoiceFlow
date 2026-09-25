# VoiceFlow — Delivery Ports & Desktop Side-Effect Boundary

Architecture 2.8 map for ChatGPT/Codex and developers. Read this when planned realtime text is correct but paste/voice action behavior, target focus, clipboard fallback or commit-after-delivery semantics are involved.

## Pipeline

```text
RealtimeTextPipeline
      ↓
RealtimeInsertionPlan / voice command
      ↓
RealtimeDeliveryController
      ↓
TextInsertionPort / VoiceActionPort
      ↓
desktop_delivery.py
      ↓ lazy Windows helpers
windows_insertion.py + clipboard + pyautogui
      ↓
current active field / desktop action
```

The first half is deterministic/headless. The last adapter layer performs external desktop effects.

## Ports: app/ports.py

### DeliveryResult

Structured result of a desktop effect:
- `ok`;
- stable `code`;
- human/debug `error`;
- foreground HWND evidence;
- focus HWND evidence.

Do not replace this with a bare boolean in new realtime paths; the evidence is useful for diagnostics and tests.

### TextInsertionPort

```python
paste_current(text: str) -> DeliveryResult
```

The current product contract intentionally targets the **currently focused** field when each realtime fragment is ready. It does not restore the window captured at recording start.

Whitespace-only text is valid because voice commands may deliver newline, paragraph, space or tab. Only the empty string is invalid.

### VoiceActionPort

```python
execute(command: dict[str, object]) -> DeliveryResult
```

Supports the already-parsed command forms:
- text;
- key;
- hotkey;
- sequence.

Parsing and safety rules remain in `voice_commands.py` / `RealtimeTextPipeline`; this port executes an already-approved action.

## Headless coordinator: app/realtime_delivery.py

`RealtimeDeliveryController` depends only on the two ports and realtime insertion-plan DTO.

### deliver_insertion()

Takes exact `RealtimeInsertionPlan.paste_text`, calls `TextInsertionPort`, and returns `InsertionDeliveryOutcome`.

Critical transaction rule:

```text
delivery failed  → committed_text = ""
delivery success → committed_text = plan.text
```

The caller records session commit only on success. Therefore failed external paste does not poison overlap/dedupe state.

### execute_voice_command()

Calls `VoiceActionPort` and returns `VoiceCommandDeliveryOutcome` with command label/kind/phrase, reset-message-context flag, error code and target evidence.

The controller does not show notifications, mutate Tk widgets or reset session state itself.

## Concrete adapter: desktop_delivery.py

### CurrentTargetTextInsertionAdapter

Responsibilities:
1. reject only truly empty text;
2. copy exact payload to clipboard;
3. wait the existing short delivery delay;
4. send native/fallback Ctrl+V;
5. return structured target/error evidence.

Injection seams:
- clipboard object;
- target getter;
- paste sender;
- sleep function.

This permits deterministic tests without Windows or clipboard access.

### CurrentTargetVoiceActionAdapter

For `kind=text`, delegates to the text insertion port so newline/space/tab behavior uses one delivery path.

For key/hotkey/sequence, uses injected automation and current target evidence.

Injection seams:
- TextInsertionPort;
- automation object;
- target getter;
- sleep function.

## Lazy desktop imports

This boundary exists partly to keep headless tests independent.

`desktop_delivery.py` may optional-import pyperclip/pyautogui directly. It must **not** import the shared audio `dependencies.py`, because that would eagerly require NumPy/sounddevice.

`windows_insertion.py` helpers are resolved inside default delivery functions only when actual desktop delivery occurs. A top-level eager Windows-helper import would make offline delivery tests initialize unrelated runtime dependencies.

Repository guards enforce this distinction.

## Composition

`ApplicationServices` now carries:
- recorder;
- transcriber;
- cleaner;
- notification factory;
- tray factory;
- text inserter;
- voice action executor.

The default composition root builds one text inserter and injects the same instance into the voice action adapter, so text voice commands and normal realtime text share identical paste semantics.

Tests may inject fake ports.

## Presentation boundary

`app/streaming.py` may:
- render planned text in Tk widgets;
- log delivery result;
- update status/notifications;
- call `record_commit()` after successful delivery;
- reset message context after a successful command whose plan requests it.

It must not:
- import pyautogui;
- call `get_paste_target()`;
- call `send_ctrl_v_native()`;
- copy to clipboard directly;
- decide key/hotkey/sequence implementation.

## Removed legacy path

Architecture 2.8 removed:
- unused `_process_audio_worker`;
- worker `RESULT` and `ERROR` kinds/dispatch branches associated with it;
- unused async `STREAM_FINISHED` / `STREAM_FINISH_TIMEOUT` finalizer.

Why: production stop is realtime-only and no caller remained for that second final transcription/result path.

Do not restore it accidentally while debugging delivery. A future final-transcription feature would need an explicit product design, separate tests and session/race semantics.

## Offline proof

`tests/test_realtime_delivery.py`:
- exact paste payload forwarded;
- commit text returned only on success;
- failed paste cannot advance committed text;
- command reset/target metadata preserved.

`tests/test_desktop_delivery.py`:
- whitespace-only text is valid;
- clipboard payload survives failed paste sender for manual fallback;
- text voice command delegates to insertion port;
- key/hotkey/sequence use injected automation;
- missing automation is a structured failure.

`tests/test_repository_contract.py`:
- streaming contains no direct pyautogui/Windows paste calls;
- legacy result/finalizer branches stay removed;
- realtime delivery remains headless;
- desktop adapter does not regain audio-runtime or eager Windows-helper imports.

## Runtime evidence still required

Automated proof does not guarantee:
- real clipboard ownership under another application;
- Chrome/Firefox/Telegram/editor acceptance of Ctrl+V;
- privilege/elevation mismatch;
- foreground/focus race during rapid app switching;
- real pyautogui key delivery;
- IME/special keyboard layout behavior.

These remain **NOT VERIFIED** until interactive Windows testing.
