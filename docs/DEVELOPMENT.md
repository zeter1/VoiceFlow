# Разработка и проверка VoiceFlow

## Установка

~~~powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip setuptools wheel
py -m pip install -r requirements.txt
~~~

## Запуск

~~~powershell
py voiceflow.py
~~~

voiceflow.py — compatibility launcher; implementation находится в voiceflow_app.

## Offline verification

~~~powershell
python -m compileall -q voiceflow.py voiceflow_app tests
python -m unittest discover -s tests -v
# Pure Architecture 2.0 regressions live in:
# tests/test_realtime_core.py
# tests/test_recording_state.py
# tests/test_hotkey_state.py
# Architecture 2.2 import boundaries: tests/test_repository_contract.py
~~~

## Evidence по типу изменения

- text/parsers: targeted unit + full offline suite;
- hotkey: offline suite + Windows runtime + hotkey_trace;
- audio: real microphone start/stop;
- streaming/dedupe: regression helpers + representative dictation/log review;
- CUDA: target GPU/model runtime;
- insertion: representative target applications + insertion log;
- packaging: successful package run + packaged --self-test.

## Package/release

Code change -> CHANGELOG.md.
Новый Windows binary -> также RELEASE_NOTES_RU.md + commit marker [package].
После push проверить final Actions run и release assets/checksum.

## Evidence labels

PASS = фактически проверено на текущем source state.
FAIL = проверка упала.
NOT VERIFIED = слой не проверялся.

Green compile не доказывает microphone/CUDA/hotkey behavior.

## Privacy

Логи проблем и особенно dictation_text.txt могут содержать пользовательский текст. Не публиковать их без просмотра.

## Import discipline

Внутри voiceflow_app не использовать wildcard imports и не импортировать runtime.py. Использовать canonical owner из [IMPORT_BOUNDARIES.md](IMPORT_BOUNDARIES.md). runtime.py существует только для внешней обратной совместимости.

### Проверка startup/logging

- canonical folder: `Логи проблем/`;
- старый `voiceflow_logs/` остаётся в `.gitignore` для приватности старых локальных логов;
- entrypoint должен брать single-instance lock до diagnostics/dependencies/Tk imports;
- import `voiceflow_app.entrypoint` не должен сам создавать/очищать log files.
