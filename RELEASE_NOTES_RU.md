# Изменения Windows-сборки

## Диагностика логов и startup hardening

- По присланному runtime-логу не обнаружены crash, ERROR/WARNING или traceback; CUDA 12/cuDNN runtime найден, preflight и `large-v3` на `cuda/int8_float16` завершились успешно.
- Исправлена startup-проблема: single-instance lock теперь берётся **до** тяжёлых imports и до создания runtime-логов.
- Второй случайно запущенный экземпляр больше не должен очищать `_last_run` работающей программы до того, как будет заблокирован.
- Убраны import-time side effects из `diagnostics.py`: логи и snapshot создаёт только принятый основной процесс.
- Папка `voiceflow_logs` переименована в **`Логи проблем`**. Новая версия программы создаёт и пишет диагностику именно туда.
- Старую папку `voiceflow_logs` программа автоматически не удаляет.
- Packaged self-test проверяет новое canonical имя log directory.

## Что в присланном запуске было нормальным

- CUDA preflight занял около 17.8 секунды, затем загрузка `large-v3` ещё около 7.3 секунды; warm-up выполнялся в фоне и завершился успешно.
- Hotkey F9 polling, tray show/hide, перенос notification и штатное завершение приложения не показали ошибок.
- В этом конкретном архиве запись речи не запускалась, поэтому streaming/insertion/recording файлы не содержат данных о качестве диктовки.

## Проверка сборки

GitHub Actions выполняет compile, полный offline regression/architecture suite, PyInstaller build, packaged `VoiceFlow.exe --self-test`, ZIP/SHA-256 и публикацию prerelease.

Реальная диктовка, microphone input, clipboard/focus races и CUDA performance на пользовательском ПК остаются интерактивными runtime-проверками.
