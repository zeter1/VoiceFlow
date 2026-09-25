# Изменения Windows-сборки

## Architecture 2.8 — Realtime Delivery Ports & Side-Effect Isolation

- Windows paste и voice-action side effects вынесены за явные `TextInsertionPort` / `VoiceActionPort`.
- Добавлен concrete `desktop_delivery.py` для clipboard/current-target/Ctrl+V/pyautogui и headless `RealtimeDeliveryController` для application orchestration.
- `app/streaming.py` больше напрямую не импортирует pyautogui, Windows focus helpers или native paste sender.
- Успешный paste остаётся единственным моментом, когда текст записывается как committed; failed paste не загрязняет realtime dedupe state.
- Добавлены fake-backed regression tests для exact paste payload, failed delivery, whitespace voice commands, key/hotkey/sequence actions и reset-message-context metadata.
- Удалён подтверждённо мёртвый legacy final-result путь (`_process_audio_worker`, RESULT/ERROR queue handling и старый async finish-finalizer), который не имел вызывающих production paths после перехода на realtime-only stop.

### Corrective compile fix

- Исправлен дублированный `elif stream_warning`, который остановил первый Architecture 2.8 CI run на compile до запуска тестов.
- Runtime behavior не менялся; повторная сборка проходит полный validation ladder.

### Corrective dependency-boundary fix

- Убран import общего `dependencies.py` из desktop delivery: он тянул NumPy/sounddevice и ломал offline delivery tests до установки packaging dependencies.
- `pyperclip` и `pyautogui` теперь загружаются локально как optional desktop dependencies.
- Добавлен architecture guard против возврата этой связности.

### Corrective lazy Windows-adapter fix

- Windows target/native paste adapter теперь подключается лениво только в момент реальной вставки.
- Offline delivery tests больше не импортируют Windows/audio runtime graph и используют structural fake target.
- Добавлен guard против eager `windows_insertion` import в delivery adapter.

### Corrective repository-oracle fix

- Architecture guard теперь запрещает только eager top-level `windows_insertion` import.
- Правильный lazy function-local import разрешён и отдельно сохраняет offline importability.
- Предыдущий run дошёл до полного offline suite; delivery/runtime tests были зелёными, ошибочным был только guard.

## Проверка сборки

GitHub Actions выполняет compile, полный offline regression/architecture suite, PyInstaller build, packaged `VoiceFlow.exe --self-test`, ZIP/SHA-256 и публикацию prerelease.

Реальный микрофон, Whisper/CUDA, global hotkey, clipboard/focus races и вставка в конкретные Windows-приложения требуют отдельной интерактивной runtime-проверки.
