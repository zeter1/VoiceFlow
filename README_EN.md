**Язык / Language:** [Русский](README.md) · **English**

# VoiceFlow

**VoiceFlow** is an offline speech-to-text application for Windows built with Python and `faster-whisper`. It listens to the microphone, recognizes speech locally, and inserts confirmed text fragments directly into whichever input field currently has focus.

The application does not require an OpenAI API key for speech recognition. After the selected model is downloaded, transcription runs locally on the computer.

## What the project demonstrates

- a real-time speech pipeline with local recognition;
- `faster-whisper` execution on CPU and NVIDIA CUDA;
- streaming audio processing without waiting for the end of the entire dictation session;
- Windows integration: global hotkeys, active input fields, tray, and autostart;
- coordination of background workers, queues, and the UI;
- a privacy-first design: recognition runs locally after model download;
- separate diagnostics for hotkeys, capture, inference, and text insertion.

## Features

- local speech recognition through `faster-whisper`;
- real-time voice input;
- text insertion into the currently active input field;
- switching between applications during one dictation session;
- configurable global hotkey;
- microphone selection;
- CPU and NVIDIA CUDA modes;
- Whisper model and quality/speed profile selection;
- voice commands for punctuation and editing;
- optional local text correction through `language-tool-python`;
- native Windows insertion with fallback methods;
- system tray and background operation;
- autostart through `HKCU\Run`;
- detailed diagnostics for hotkeys, recording, recognition, and insertion.

## Installation

1. Install an x64 Python 3.11–3.13 build for Windows.
2. Download the project with **Code → Download ZIP** or Git:

```bash
git clone https://github.com/zeter1/VoiceFlow.git
cd VoiceFlow
```

3. Create a virtual environment and install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip setuptools wheel
py -m pip install -r requirements.txt
```

For NVIDIA acceleration, use a current NVIDIA driver and a compatible CUDA/cuDNN configuration as described in the project documentation.

The selected Whisper model may be downloaded automatically on first use.

## Launch

```powershell
py voiceflow.py
```

## Usage

1. Start VoiceFlow.
2. Select the microphone, Whisper model, and CPU or CUDA mode.
3. Place the cursor in any text field — for example a browser, Telegram, ChatGPT, document, or code editor.
4. Press the global hotkey. A fresh configuration uses Ctrl+Shift+Space by default, and the shortcut can be changed in the UI.
5. Speak normally.
6. Stable recognized fragments are inserted into the active field as dictation continues.
7. You can switch to another application or field; subsequent fragments are inserted there.
8. Press the hotkey again to stop recording.

The application deliberately avoids inserting the entire final transcript again after stopping, preventing duplication of fragments that were already sent.

## Architecture

Main data flow:

```text
microphone
   ↓
audio capture
   ↓
realtime buffering / worker queue
   ↓
faster-whisper inference
   ↓
stability + deduplication
   ↓
voice commands / cleanup
   ↓
Windows text insertion
   ↓
active application
```

Subsystem boundaries, dictation lifecycle, CPU/CUDA paths, deduplication, and the insertion layer are described in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). AI/Codex navigation is documented in [`AGENTS.md`](AGENTS.md) and [`docs/AI_CONTEXT.md`](docs/AI_CONTEXT.md).

## Privacy

After the Whisper model is downloaded:

- recognition runs locally;
- no OpenAI API key is required;
- recorded audio is not sent to the OpenAI API;
- settings and logs remain on the user's computer.

Before publishing diagnostic artifacts, check that they do not contain private dictated text or other user data. See [`SECURITY.md`](SECURITY.md).

## Recommended settings

### NVIDIA GPU

- device: `cuda`;
- compute type: `int8_float16`;
- model: `medium` or `large-v3`;
- language: `ru` for Russian dictation;
- profile: Balanced or Quality.

### CPU

- device: `cpu`;
- compute type: `int8`;
- model: `small` or `base`;
- profile: Balanced or Faster.

## Voice commands

Commands cover punctuation, new lines and paragraphs, space and Tab, Backspace and word deletion, undo, select all, copy/paste/save, cursor movement, and clearing the current line or input field.

## Diagnostics

Every launch creates a separate diagnostic folder:

```text
voiceflow_logs/run_YYYY-MM-DD_HH-MM-SS_PID/
```

The latest session is available through:

```text
voiceflow_logs/_last_run/
```

Diagnostics are separated by area: hotkeys, recording, streaming recognition, worker queues, text insertion, notifications, and general failures.

## Settings

Settings are stored in:

```text
voiceflow_settings/settings.json
```

User settings and runtime logs are excluded from Git.

## Project verification

```powershell
python -m compileall -q voiceflow.py voiceflow_app tests
python -m unittest discover -s tests -v
```

CI performs syntax/compile checks and offline repository-contract regression tests without downloading Whisper models, accessing a microphone, or initializing CUDA.

## Limitations and verification level

- recognition quality and latency depend on the model, CPU/GPU, and audio device;
- the CUDA path requires a compatible local NVIDIA/CUDA/cuDNN environment;
- text insertion and global hotkeys depend on the behavior of the target Windows application;
- CI does not prove real microphone, CUDA, tray, hotkey, or third-party application insertion behavior — those scenarios require runtime verification on Windows;
- `voiceflow.py` is now a thin compatibility entrypoint; implementation is split across runtime/services/ui/app boundaries, with main-window behavior divided into responsibility-focused mixins.

## Documentation and support

- [VoiceFlow architecture](docs/ARCHITECTURE.md)
- [AI/Codex repository map](docs/AI_CONTEXT.md)
- [Development and verification](docs/DEVELOPMENT.md)
- [Repository instructions for AI agents](AGENTS.md)
- additional CUDA/cuDNN, voice-command, model, microphone, and diagnostic guidance is under [`docs/`](docs/)
- [Security and privacy](SECURITY.md)
- [Support and diagnostics](SUPPORT.md)

## License

The project is not distributed under an open-source license. The source code is published for portfolio review, implementation study, and code review.
