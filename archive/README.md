# Archive

Здесь лежат файлы, которые больше не используются актуальной версией проекта, но оставлены для истории и ручного сравнения.

## legacy

Старые версии скриптов и ранние systemd unit-файлы:

- `barrier.py`: первая монолитная версия с одним MAC-адресом в коде.
- `barrier.service` и `barrier-panel.service`: старые unit-файлы до установки через `install.sh`.
- `old_versions/`: ранние Python/C++ версии и тестовые скрипты.

## production_snapshot

Снимок прежнего production-каталога `barrier_prod`, включая копии старых сервисов и SQLite-базу.

Актуальный код находится в корне репозитория:

- `barrier_service.py`
- `panel.py`
- `barrier_*.py`
- `install.sh`
- `requirements.txt`
- `tests/`
