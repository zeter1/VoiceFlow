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

voiceflow_logs и особенно dictation_text.txt могут содержать пользовательский текст. Не публиковать их без просмотра.
