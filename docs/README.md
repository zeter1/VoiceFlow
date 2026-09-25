# Документация VoiceFlow

Каталог разделён по назначению, чтобы техническая документация не смешивалась с пользовательским обучением.

## Для разработки и нейросетей

- [ARCHITECTURE.md](ARCHITECTURE.md) — physical architecture, owners, state/thread boundaries.
- [AI_CONTEXT.md](AI_CONTEXT.md) — быстрый task → owner map для ChatGPT/Codex.
- [DEVELOPMENT.md](DEVELOPMENT.md) — команды разработки, verification ladder и release discipline.
- [IMPORT_BOUNDARIES.md](IMPORT_BOUNDARIES.md) — canonical owners, dependency directions и запреты на facade/wildcard coupling.
- [SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md) — service ports, adapters, injection seams и offline testing.
- [APPLICATION_SESSION.md](APPLICATION_SESSION.md) — composition root, session state ownership, headless lifecycle/race tests.
- [REALTIME_PIPELINE.md](REALTIME_PIPELINE.md) — headless RealtimeWorkerEngine, audio/commit policy, frame-cursor recovery, typed queue and stale-session/cancellation contracts.
- [REALTIME_TEXT_COMMIT.md](REALTIME_TEXT_COMMIT.md) — cleanup/dedupe/voice-command/punctuation planning and exact paste-payload boundary.
- [DELIVERY_PORTS.md](DELIVERY_PORTS.md) — TextInsertion/VoiceAction ports, concrete desktop adapters, commit-on-success semantics and fake-backed tests.
- [../AGENTS.md](../AGENTS.md) — стабильные repo-инварианты и правила AI coding agents.

## Пользовательское обучение

Все пошаговые инструкции, настройка, CUDA, команды, диагностика и большая объединённая инструкция перенесены в отдельный каталог:

- [user-guide/README.md](user-guide/README.md)
