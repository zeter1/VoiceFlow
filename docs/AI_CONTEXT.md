# VoiceFlow — карта проекта для ChatGPT/Codex

Сначала прочитай ../AGENTS.md, затем только релевантный раздел этого файла и owner-модуль. Не перечитывай весь проект без причины.

## Pipeline

microphone -> AudioRecorder -> realtime buffer -> LocalTranscriber -> stability/dedupe -> voice command/cleanup -> Windows insertion -> active application.

## Физическая карта

- composition.py: injectable ApplicationServices/default composition root; concrete desktop service construction belongs here.
- runtime.py: external compatibility facade only; internal modules must not import it.
- config.py: paths and runtime constants.
- diagnostics.py: logs, diagnostics and exception hooks.
- dependencies.py: third-party package loading only.
- audio_devices.py: microphone catalog / PortAudio discovery adapter.
- cuda_runtime.py: CUDA DLL/preflight adapter and backend candidate policy.
- hotkey_config.py: shortcut normalization/VK mapping.
- settings.py: persisted and runtime settings.
- voice_commands.py: voice command vocabulary/parsing.
- windows.py: compatibility re-export for historical callers.
- windows_startup.py: registry startup command/state.
- windows_insertion.py: focused HWND/caret target and native paste.
- core/realtime.py: pure realtime text/dedupe/punctuation decisions.
- core/realtime_policy.py: timing profiles, speech/chunk commit and final-cancellation decisions.
- core/recording_state.py: pure recording state/repair classification.
- core/hotkey_state.py: pure hotkey edge/debounce decisions.
- services/audio.py: microphone capture.
- services/audio_analysis.py: realtime audio statistics / noisy-room pause evidence.
- services/transcription.py: model lifecycle and inference; backend environment is injected.
- services/text_cleaner.py: punctuation/fillers/grammar/formatting.
- services/contracts.py: Protocol contracts for the three service boundaries.
- app/ports.py: Notification/Tray Protocol ports.
- app/session_controller.py: headless session lifecycle, session_id, committed realtime text and service pipeline.
- app/ui.py: Tk interface.
- app/controls.py: microphone/hotkey controls.
- app/hotkeys.py: registration/polling/debounce/dispatch.
- app/recording.py: recording state transitions and warm-up.
- app/realtime_worker.py: headless frame cursor, transcription context, chunk processing, WAV cleanup and typed message production.
- app/realtime_text_pipeline.py: headless recognized-text → display/command/insertion planning, including exact paste payload.
- app/streaming.py: thin realtime application adapter plus actual Tk rendering/paste/voice-command side effects.
- app/worker_dispatch.py: main-thread typed queue decode, stale-session/after-stop filtering and UI/application routing.
- worker_messages.py: message kinds/payloads, legacy coercion seam and pure session-result gates.
- app/actions.py: copy/paste/settings/shutdown.
- ui/notifications.py and ui/tray.py: UI infrastructure.
- entrypoint.py: startup + packaged self-test.

## State owners

VoiceFlowOfflineApp owns Tk/UI orchestration. HeadlessSessionController owns session identity, capture lifecycle and committed realtime text. SettingsStore owns persistence. AudioRecorder owns capture stream/frames. LocalTranscriber owns model/inference state. Notification/tray managers do not own recording truth.

When adding a persisted setting: AppSettings -> RuntimeSettings if worker needs it -> load/save/UI -> docs -> regression contract where possible.

## Thread boundary

Tk main thread handles widgets. Capture/inference use background execution. Worker -> UI/app communication goes through queue/scheduled callbacks. Do not fix a race with arbitrary sleep before understanding the state transition.

## Realtime contract

Confirmed fragments are inserted during recording. `services/audio_analysis.py` owns audio evidence, `core/realtime_policy.py` owns commit/cancel policy, `core/realtime.py` owns primitive pure text algorithms, `HeadlessSessionController` owns session identity/transcription service coordination/committed inserted text, `RealtimeWorkerEngine` owns the background frame/transcription loop, `RealtimeTextPipeline` owns recognized-text cleanup/command/dedupe/punctuation/exact insertion planning, `app/worker_dispatch.py` applies the plan on the main thread, and `app/streaming.py` performs the actual UI/paste/voice-command side effects. Stop must release session state without pasting the full transcript again.

## Hotkey diagnostics

Symptom "works once", double start/stop or stuck hotkey: inspect app/hotkeys.py, hotkey_trace.jsonl and recording_state.jsonl first.

## Insertion diagnostics

For wrong target/paste failure: windows_insertion.py + app/actions.py + insertion.jsonl. Check foreground restoration, clipboard/native Ctrl+V fallback and privilege mismatch.

## Logs

Per run: voiceflow_logs/run_YYYY-MM-DD_HH-MM-SS_PID.
Latest mirror: voiceflow_logs/_last_run.

voiceflow.txt = general lifecycle/errors.
diagnostics.json = environment/dependencies.
hotkeys.jsonl/hotkey_trace.jsonl = hotkey evidence.
recording_state.jsonl = recording transitions.
streaming.jsonl = realtime recognition.
worker_queue.jsonl = worker messages/backlog.
insertion.jsonl = paste path.
notifications.jsonl = notification lifecycle.
dictation_text.txt = private dictated text; review before sharing.

## Build/release

Normal source push: compile + offline unittest.
[package] push/workflow_dispatch: install deps -> PyInstaller onedir -> VoiceFlow.exe --self-test -> ZIP/SHA-256 -> prerelease.

Green package CI does NOT prove a real microphone, a downloaded Whisper model, user CUDA, global desktop hotkeys, tray behavior or insertion into arbitrary third-party windows. Mark those NOT VERIFIED without runtime evidence.

## AI completion handoff

Return: source commit -> actual delta -> checks PASS/FAIL -> NOT VERIFIED -> residual risk/next engineering stage.

## Educational documentation

User-facing guides are isolated under user-guide/. Technical/AI work should start from AGENTS.md, ARCHITECTURE.md, AI_CONTEXT.md and DEVELOPMENT.md rather than scanning tutorial files.

Architecture 2.2 rule: every internal module uses explicit owner imports; context.py is gone and runtime.py is external compatibility only. The enforced dependency map is documented in IMPORT_BOUNDARIES.md.

## Import boundary shortcut

Перед добавлением cross-module import открой [IMPORT_BOUNDARIES.md](IMPORT_BOUNDARIES.md). Если symbol уже имеет canonical owner, импортируй owner напрямую; не прокладывай зависимость через runtime.py.

## Service/adapters rule

Hardware/environment knowledge must stop at adapter boundaries. `LocalTranscriber` may ask a backend-runtime port for candidate/preflight results, but it must not call `find_windows_dll` or `subprocess.run` itself. `app/controls.py` asks `audio_devices.py` for microphones; `dependencies.py` must not own enumeration. See [SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md).

## Headless session route

Для start/stop race, repeated start, finalizing, recovery или committed-text regressions сначала открой [APPLICATION_SESSION.md](APPLICATION_SESSION.md) и `app/session_controller.py`. Не начинай с Tk widgets.

Для testable full path без GUI используй injected fakes: recorder → frames_to_wav → transcriber → cleaner → record_commit → stop. Production composition создаётся только в `composition.py`.

## Realtime debugging route

Пауза/шум/слишком ранний или поздний commit → `services/audio_analysis.py` + `core/realtime_policy.py` + `streaming.jsonl`.

Повтор/хвост/пунктуация primitives → `core/realtime.py`; full worker-result → exact insertion decision → `app/realtime_text_pipeline.py`.

Результат старой session, поздний message после stop, queue payload → `worker_messages.py` + `app/worker_dispatch.py` + `worker_queue.jsonl`.

Frame cursor / worker retry / transcription context / temp WAV cleanup → `app/realtime_worker.py`.
Thread creation/stop/finalizer adapter → `app/streaming.py`.

Полная карта и invariants: [REALTIME_PIPELINE.md](REALTIME_PIPELINE.md).

## Realtime worker failure route

Если после одной transcription error поток перестал печатать: сначала `tests/test_realtime_worker.py` и `app/realtime_worker.py`. Проверяй cursor semantics: noise/filtered chunk должен advance, transcription exception — оставить cursor для retry и отправить исходный `StreamWarningPayload`.

Если ошибка говорит про cleanup/WAV после другой ошибки, проверь `wav_path: Optional[Path] = None` и cleanup only-after-successful-path assignment. Не маскируй исходную transcription exception broad suppression.

## Realtime text-commit debugging route

Неправильная точка/регистр/лишний повтор/вставляется raw вместо cleaned → сначала `tests/test_realtime_text_pipeline.py` + `app/realtime_text_pipeline.py`.

Voice command попал в обычный текст или текст перед командой потерялся → `plan_stream_result()` + `voice_commands.py`.

Pause metadata видно в worker payload, но вставленная пунктуация неверна → проверь, что `worker_dispatch.py` передаёт `commit_meta` без потерь. Полная карта: [REALTIME_TEXT_COMMIT.md](REALTIME_TEXT_COMMIT.md).
