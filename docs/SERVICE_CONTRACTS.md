# VoiceFlow — Service Contracts & Adapters

Документ для ChatGPT/Codex и разработчиков. Цель — отделять **что должна делать подсистема** от **как конкретно Windows/PortAudio/CUDA выполняют side effect**.

## Service ports

### AudioRecorderContract

Owner: `services/contracts.py`. Implementation: `services/audio.py::AudioRecorder`.

Contract: start/stop capture; сохранить/отбросить buffered frames; получить frames since index; преобразовать frames в временный WAV; сообщить `is_recording`, sample rate и device.

Не добавлять сюда enumeration микрофонов. Enumeration принадлежит `audio_devices.py`.

### TranscriberContract

Owner: `services/contracts.py`. Implementation: `services/transcription.py::LocalTranscriber`.

Contract: WAV + language/model/quality/runtime options → text.

LocalTranscriber владеет model cache, failed backend cache, inference parameters и transcription lock. Он **не владеет** поиском CUDA DLL или процессом environment preflight.

Injection seams:
- `backend_runtime` — backend plan/preflight/mark_verified;
- `model_factory` — factory compatible with faster-whisper WhisperModel construction.

Default production adapter: `cuda_runtime.py::CudaRuntimeProbe`.

### TextCleanerContract

Owner: `services/contracts.py`. Implementation: `services/text_cleaner.py::LocalTextCleaner`.

Contract: raw text + mode/language/custom terms/deep grammar → cleaned text.

Этот service должен оставаться максимально platform-independent; Windows/device imports здесь являются architecture smell.

## External adapters

### audio_devices.py

Ответственность: получить PortAudio/sounddevice devices и превратить их в стабильный список `(index, label)`.

Pure-ish seam: `list_input_devices(backend)`. В тестах backend фальшивый; реальный `sounddevice` импортируется только production wrapper `get_input_devices()`.

### cuda_runtime.py

Ответственность: определить доступность обязательных CUDA runtime DLL на Windows; построить ordered backend candidate plan; безопасно проверить CUDA model load в child process; кэшировать уже подтверждённые compute types.

Не переносить subprocess/DLL lookup обратно в LocalTranscriber.

### windows_startup.py / windows_insertion.py

`windows_startup.py` владеет startup command и HKCU Run registry state. `windows_insertion.py` владеет foreground/focus target и native Ctrl+V. `windows.py` — только compatibility re-export.

## Offline verification

- `tests/test_audio_devices.py` — fake PortAudio catalog, default device и host API failures;
- `tests/test_cuda_runtime.py` — CPU/CUDA candidate policy, missing DLL short-circuit, preflight cache;
- `tests/test_service_contracts.py` — concrete service classes покрывают Protocol methods и LocalTranscriber сохраняет injection seams;
- `tests/test_repository_contract.py` — ownership/architecture guards.

Эти tests не доказывают реальный microphone/CUDA runtime, но ловят structural regressions раньше PyInstaller.

## Runtime verification after adapter changes

Audio: реальный список микрофонов, default device, start/stop, WAV/final chunk и noisy/quiet microphone behavior.

CUDA: CPU fallback без CUDA, валидный CUDA 12/cuDNN path, broken/missing DLL path, real model inference и повторная session без лишнего preflight.

Windows insertion: Chrome/Firefox/Telegram/editor, смена активного окна во время диктовки, minimized/maximized target и privilege mismatch.

Startup: enable/disable HKCU Run, frozen EXE command и source/pythonw command.

Без этих runtime checks claims помечать NOT VERIFIED.

## Application composition

`composition.py::ApplicationServices` groups the three service contracts plus notification/tray factories. The default builder imports concrete implementations lazily; tests may supply fakes directly.

`VoiceFlowOfflineApp(root, services=...)` is the injection point. If a new external service is needed, add a narrow port/contract and wire the default implementation in composition rather than constructing it in a mixin.

## HeadlessSessionController

Owner: `app/session_controller.py`.

Responsibilities:
- monotonically increasing session identity;
- idle/starting/recording/finalizing transition guard;
- recorder start/stop/recovery coordination;
- committed realtime text / inserted_any ownership;
- frames → WAV → TranscriberContract → optional TextCleanerContract pipeline.

Non-responsibilities:
- Tk status/buttons/timers;
- hotkey polling;
- notification rendering;
- actual paste target / Ctrl+V;
- pure dedupe/punctuation policy (that stays in `core/realtime.py`).

Offline proof: `tests/test_session_controller.py` covers start→frames→transcription→commit→stop, double-start rejection, stop/restart, failed-start recovery, failed-stop recovery, finalizing race and deferred frame discard.

## Realtime audio-analysis adapter

`services/audio_analysis.py` is adjacent to AudioRecorder but is not another capture service. It turns frame samples into signal facts used by the pure realtime policy: duration, RMS, peak, active ratio, raw trailing silence and adaptive speech trailing silence.

Its threshold/stat-summary helpers are dependency-free. The production `stream_audio_stats()` wrapper imports NumPy lazily, so policy/unit tests do not require audio runtime initialization.

Do not move commit timing into this adapter. Audio analysis reports facts; `core/realtime_policy.py` decides what those facts mean for streaming.

## RealtimeSessionPort

Owner: `app/realtime_worker.py` as a narrow internal Protocol. Production implementation is structurally satisfied by `HeadlessSessionController.transcribe_frames()`.

Purpose: let the worker engine orchestrate frames → WAV → transcription without depending on Tk/application shell or concrete Whisper implementation.

The worker still receives `AudioRecorderContract`; microphone implementation details stay in `services/audio.py`. Tests can supply recorder/session fakes plus deterministic audio stats without importing or initializing hardware.
