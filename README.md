# VoiceFlow

Offline Windows voice dictation application built with Python and `faster-whisper`.

VoiceFlow listens to the microphone, recognizes speech locally and inserts confirmed text fragments directly into the currently focused input field while recording. It does **not** require an OpenAI API key and does not send recorded audio to the OpenAI API.

## Highlights

- Local speech recognition with **faster-whisper**.
- Realtime dictation: recognized chunks are inserted while recording, not only after stopping.
- Text follows the **current cursor/focused input field**: you can switch between Telegram, a browser, ChatGPT, documents, notes or code editors during one dictation session.
- Configurable global hotkey (F9 is the recommended/default workflow in the documentation).
- Microphone selection and refresh.
- CPU and NVIDIA CUDA modes.
- Configurable Whisper model, compute type and realtime quality/speed profile.
- Voice commands for punctuation, new lines, editing and common keyboard actions.
- Optional offline text cleanup and grammar correction with `language-tool-python`.
- Native Windows paste/injection with fallback methods for applications where ordinary paste is unreliable.
- Movable recording notification with persisted position.
- System tray support and background operation.
- Optional autostart through the Windows `HKCU\Run` registry key.
- Persistent settings between launches.
- Detailed per-launch diagnostic logs designed to help investigate hotkey, recording, streaming and insertion problems.

## How it works

1. Put the cursor in any text field.
2. Press the configured global hotkey.
3. Speak normally.
4. VoiceFlow records audio and processes stable speech fragments locally with `faster-whisper`.
5. Confirmed fragments are inserted into the input field that is focused **at that moment**.
6. You can switch to another application and move the cursor while recording; subsequent fragments will be inserted there.
7. Press the hotkey again to stop.

The application intentionally avoids a final large paste after stopping, which prevents already inserted realtime text from being duplicated.

## Privacy / offline behavior

Speech recognition is local after the Whisper model has been downloaded.

- No OpenAI API key is required.
- Recorded speech is not sent to the OpenAI API.
- `faster-whisper` may download the selected model the first time it is used.
- After the model is available locally, transcription runs on the local machine.

## Requirements

- Windows 10/11.
- Python x64 3.11, 3.12 or 3.13 recommended by the bundled documentation.
- A working microphone.
- Python dependencies from `requirements.txt`.

For NVIDIA GPU acceleration, the bundled documentation recommends a recent NVIDIA driver, CUDA Toolkit 12.x and cuDNN 9 for CUDA 12.x.

## Installation

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip setuptools wheel
py -m pip install -r requirements.txt
```

## Run

```powershell
py voiceflow.py
```

If the global hotkey or text insertion does not work in a specific elevated application, try running VoiceFlow with the same privilege level (for example, as Administrator).

## Main dependencies

- `numpy` — audio/data processing.
- `sounddevice` — microphone recording.
- `faster-whisper` — local speech recognition.
- `pyperclip` — clipboard operations.
- `pyautogui` — fallback keyboard actions and voice commands.
- `keyboard` — hotkey support in some modes.
- `language-tool-python` — optional stronger grammar cleanup.
- `pystray` — system tray integration.
- `Pillow` — tray icon rendering.

## Recommended settings

The bundled documentation suggests these starting points:

### NVIDIA GPU

- Device: `cuda`
- Compute: `int8_float16`
- Model: `medium` or `large-v3`
- Language: `ru` for Russian dictation
- Realtime profile: Balanced or Quality
- Fragment interval: about 4–6 seconds for higher quality

### CPU

- Device: `cpu`
- Compute: `int8`
- Model: `small` or `base`
- Realtime profile: Balanced or Faster

## Voice commands

VoiceFlow includes commands for common dictation and editing actions, including:

- punctuation: period, comma, question mark, exclamation mark;
- new line and new paragraph;
- space and tab;
- Backspace / delete word;
- undo, select all, copy, paste and save;
- cursor movement and line navigation;
- line/field clearing actions.

The full Russian-language usage notes are stored in the `docs/` directory.

## Diagnostics

VoiceFlow creates a separate log directory for each launch:

```text
voiceflow_logs/run_YYYY-MM-DD_HH-MM-SS_PID/
```

A convenient copy/pointer for the latest run is kept under:

```text
voiceflow_logs/_last_run/
```

The diagnostics include separate logs for areas such as:

- hotkeys and detailed hotkey traces;
- recording state;
- notifications;
- realtime streaming;
- worker queues;
- text insertion;
- crashes and general diagnostics.

These runtime logs and local settings are excluded from Git by `.gitignore`.

## Local settings

Settings are stored in:

```text
voiceflow_settings/settings.json
```

They are intentionally excluded from the repository because they are user-specific runtime data.

## Verification

The repository includes a Windows GitHub Actions workflow that compiles the main source file on every push and pull request:

```powershell
python -m py_compile voiceflow.py
```

The CI check is intentionally lightweight and does not download Whisper models, access a microphone or initialize CUDA. Audio devices, GPU execution, hotkeys and realtime insertion still require real Windows testing.

## Documentation

The repository includes the original Russian documentation from the application archive under `docs/`, including quick start, model warm-up, CUDA/cuDNN setup, voice commands, punctuation, logging and microphone troubleshooting.

## License

No open-source license is currently granted. The source code is published for portfolio and code-review purposes.
