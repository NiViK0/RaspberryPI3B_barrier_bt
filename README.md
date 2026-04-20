# Barrier BLE Controller

Сервис для управления шлагбаумом с Raspberry Pi или другого Linux-одноплатника. Система ищет разрешенные Bluetooth/BLE-устройства по MAC-адресам, хранит список устройств в SQLite, управляет serial-реле и дает web-панель для управления с телефона.

## Возможности

- Поиск разрешенных устройств через `bluetoothctl`.
- Список устройств в SQLite.
- Включение, отключение и удаление устройств через CLI и web-панель.
- Управление USB/serial-реле.
- Dry-run режим для проверки без реального реле.
- Автоопределение serial-порта реле.
- Журнал событий в SQLite.
- Backup базы данных.
- Web-панель со статусом системы, последними событиями и опциональным паролем.
- Автозапуск через `systemd`.
- Unit-тесты логики присутствия устройств.

## Структура проекта

```text
barrier_service.py      CLI и основной сервисный цикл
panel.py                Flask web-панель
barrier_config.py       Конфигурация из переменных окружения
barrier_db.py           SQLite, устройства, события, backup
barrier_bluetooth.py    Работа с bluetoothctl
barrier_relay.py        Serial-реле, dry-run, автоопределение порта
barrier_presence.py     Логика присутствия устройства
barrier_types.py        Общие типы и состояние
install.sh              Установка на Raspberry Pi/Linux через systemd
.env.example            Пример переменных окружения
tests/                  Unit-тесты
```

## Архитектура

```text
Телефон / BLE-устройство
        |
        v
bluetoothctl scan
        |
        v
barrier_service.py
        |
        +--> SQLite: allowed_devices, event_log
        |
        +--> serial relay
        |
        v
Шлагбаум

panel.py читает ту же SQLite-базу и вызывает barrier_service.py для команд управления.
```

## Быстрая установка

На Raspberry Pi или другом Linux-устройстве:

```bash
git clone https://github.com/NiViK0/RaspberryPI3B_barrier_bt.git /tmp/barrier
cd /tmp/barrier
sudo bash install.sh
```

Скрипт:

- установит системные пакеты;
- создаст `/opt/barrier`;
- склонирует репозиторий в `/opt/barrier/src`;
- создаст virtualenv в `/opt/barrier/venv`;
- установит `pyserial` и `flask`;
- создаст systemd-сервисы;
- инициализирует SQLite-базу;
- запустит web-панель;
- попробует запустить BLE-сервис.

Если BLE-сервис не стартует сразу, это нормально: сначала может понадобиться добавить MAC-адрес телефона и проверить реле.

## Переменные окружения

Настройки можно задавать через переменные окружения. Пример есть в `.env.example`.

Основные переменные:

```bash
BARRIER_DB_PATH=/opt/barrier/barrier.db
BARRIER_BACKUP_DIR=/opt/barrier/backups
BARRIER_SCRIPT=/opt/barrier/src/barrier_service.py

BARRIER_RELAY_PORT=/dev/ttyUSB0
BARRIER_RELAY_PORT=auto
BARRIER_RELAY_BAUDRATE=9600
BARRIER_DRY_RUN=false

BARRIER_SCAN_TIME=8
BARRIER_CHECK_INTERVAL=2
BARRIER_COOLDOWN=15
BARRIER_PULSE_TIME=2
BARRIER_MISSING_THRESHOLD=3

BARRIER_PANEL_HOST=0.0.0.0
BARRIER_PANEL_PORT=8080
BARRIER_PANEL_PASSWORD=
BARRIER_FLASK_SECRET_KEY=change-me
```

`BARRIER_RELAY_PORT=auto` включает поиск первого доступного порта из `/dev/ttyUSB*` и `/dev/ttyACM*`.

`BARRIER_DRY_RUN=true` отключает реальную активацию реле. Сервис будет логировать действия, но не писать в serial-порт.

`BARRIER_PANEL_PASSWORD` включает пароль для web-панели. Если переменная пустая, панель доступна без логина.

`BARRIER_FLASK_SECRET_KEY` нужен Flask-сессиям. Для реального устройства лучше задать свое случайное значение.

## Настройка systemd env

`install.sh` уже добавляет основные переменные в unit-файлы:

```ini
Environment=BARRIER_DB_PATH=/opt/barrier/barrier.db
Environment=BARRIER_BACKUP_DIR=/opt/barrier/backups
Environment=BARRIER_SCRIPT=/opt/barrier/src/barrier_service.py
```

Чтобы добавить пароль панели или поменять порт реле:

```bash
sudo systemctl edit barrier-panel.service
```

Пример override:

```ini
[Service]
Environment=BARRIER_PANEL_PASSWORD=strong-password
Environment=BARRIER_FLASK_SECRET_KEY=replace-with-random-string
```

Для BLE-сервиса:

```bash
sudo systemctl edit barrier.service
```

Пример override:

```ini
[Service]
Environment=BARRIER_RELAY_PORT=auto
Environment=BARRIER_DRY_RUN=false
```

После изменения:

```bash
sudo systemctl daemon-reload
sudo systemctl restart barrier.service barrier-panel.service
```

## CLI

Все команды ниже рассчитаны на установку через `install.sh`.

```bash
PY=/opt/barrier/venv/bin/python
APP=/opt/barrier/src/barrier_service.py
```

Инициализировать базу:

```bash
$PY $APP init-db
```

Добавить или обновить устройство:

```bash
$PY $APP add AA:BB:CC:DD:EE:FF "My Phone"
```

Показать устройства:

```bash
$PY $APP list
```

Включить устройство:

```bash
$PY $APP enable AA:BB:CC:DD:EE:FF
```

Отключить устройство:

```bash
$PY $APP disable AA:BB:CC:DD:EE:FF
```

Удалить устройство:

```bash
$PY $APP remove AA:BB:CC:DD:EE:FF
```

Тестовый импульс реле:

```bash
$PY $APP test-open
```

Открыть шлагбаум вручную:

```bash
$PY $APP manual-open
```

Тест без реального реле:

```bash
$PY $APP --dry-run test-open
```

Найти serial-порт реле:

```bash
$PY $APP detect-relay
```

Сделать backup базы:

```bash
$PY $APP backup-db
```

Запустить основной BLE-цикл вручную:

```bash
$PY $APP run
```

Запустить основной цикл без активации реле:

```bash
$PY $APP --dry-run run
```

## Web-панель

Ручной запуск:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/panel.py
```

Открыть с телефона или компьютера в той же сети:

```text
http://IP_УСТРОЙСТВА:8080
```

IP можно узнать так:

```bash
hostname -I
```

В панели доступны:

- список разрешенных устройств;
- добавление устройства;
- включение и отключение устройства;
- удаление устройства;
- ручное открытие шлагбаума;
- тестовое открытие;
- restart Bluetooth;
- backup базы;
- статус системы;
- последние события из SQLite.

## Пароль web-панели

По умолчанию пароль выключен.

Чтобы включить пароль:

```bash
sudo systemctl edit barrier-panel.service
```

Добавить:

```ini
[Service]
Environment=BARRIER_PANEL_PASSWORD=strong-password
Environment=BARRIER_FLASK_SECRET_KEY=replace-with-random-string
```

Применить:

```bash
sudo systemctl daemon-reload
sudo systemctl restart barrier-panel.service
```

После этого web-панель будет открывать страницу входа.

## SQLite

База по умолчанию:

```text
/opt/barrier/barrier.db
```

Таблицы:

- `allowed_devices`: разрешенные устройства;
- `event_log`: журнал событий.

Журнал событий заполняется сервисом, CLI и web-панелью. В него пишутся добавления устройств, включение/отключение, тесты реле, backup, ошибки сканирования и импульсы открытия/закрытия.

## Backup базы

CLI:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py backup-db
```

Web-панель:

```text
Быстрые действия -> Сделать backup базы
```

Файлы сохраняются в:

```text
/opt/barrier/backups
```

Имя файла выглядит примерно так:

```text
barrier-20260420-153000.db
```

## Bluetooth

Проверить адаптер:

```bash
bluetoothctl show
```

Рабочее состояние должно выглядеть так:

```text
Powered: yes
PowerState: on
```

Если видно `Powered: no` и `PowerState: off-blocked`, Bluetooth заблокирован через `rfkill` и сервис не сможет сканировать устройства. Разблокировать:

```bash
sudo rfkill list
sudo rfkill unblock bluetooth
sudo systemctl restart bluetooth
bluetoothctl power on
bluetoothctl show
```

После восстановления Bluetooth перезапустить сервис:

```bash
sudo systemctl restart barrier.service
```

Ручное сканирование:

```bash
timeout 15s bluetoothctl scan on
bluetoothctl devices
```

Если адаптер выключен:

```bash
bluetoothctl
```

Внутри `bluetoothctl`:

```text
power on
agent on
default-agent
quit
```

## Реле

Посмотреть доступные serial-порты:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Проверить автоопределение:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py detect-relay
```

Если у пользователя нет доступа к serial-порту:

```bash
sudo usermod -aG dialout $USER
```

После этого лучше перелогиниться или перезагрузить устройство.

Тест реле:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py test-open
```

Ручное открытие:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py manual-open
```

Безопасный тест без реле:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py --dry-run test-open
```

## Systemd

Основной сервис:

```bash
sudo systemctl status barrier.service
sudo systemctl restart barrier.service
sudo systemctl stop barrier.service
```

Web-панель:

```bash
sudo systemctl status barrier-panel.service
sudo systemctl restart barrier-panel.service
sudo systemctl stop barrier-panel.service
```

Включить автозапуск:

```bash
sudo systemctl enable bluetooth
sudo systemctl enable barrier.service
sudo systemctl enable barrier-panel.service
```

Логи:

```bash
journalctl -u barrier.service -f
journalctl -u barrier-panel.service -f
```

## Обновление

Если проект установлен через `install.sh`, обновить код можно повторным запуском:

```bash
cd /tmp/barrier
git pull
sudo bash install.sh
```

Или вручную:

```bash
cd /opt/barrier/src
git pull
sudo systemctl restart barrier.service barrier-panel.service
```

## Тесты

На машине разработчика:

```bash
python -m unittest discover -s tests
```

Проверка синтаксиса:

```bash
python -m py_compile barrier_config.py barrier_types.py barrier_db.py barrier_presence.py barrier_bluetooth.py barrier_relay.py barrier_service.py panel.py tests/test_presence.py
```

Тесты не требуют Raspberry Pi, Bluetooth или реле. Они проверяют чистую логику presence-состояний.

## Минимальный сценарий после установки

```bash
PY=/opt/barrier/venv/bin/python
APP=/opt/barrier/src/barrier_service.py

$PY $APP init-db
$PY $APP add AA:BB:CC:DD:EE:FF "My Phone"
$PY $APP detect-relay
$PY $APP --dry-run test-open
$PY $APP test-open

sudo systemctl restart barrier.service barrier-panel.service
```

После этого открыть:

```text
http://IP_УСТРОЙСТВА:8080
```

## Диагностика

Проверить IP:

```bash
hostname -I
ip a
```

Проверить Bluetooth:

```bash
bluetoothctl show
timeout 15s bluetoothctl scan on
bluetoothctl devices
```

Проверить serial-порт:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py detect-relay
```

Проверить сервисы:

```bash
sudo systemctl status barrier.service
sudo systemctl status barrier-panel.service
```

Проверить порт web-панели:

```bash
ss -tulpn | grep 8080
```

Посмотреть последние логи:

```bash
journalctl -u barrier.service -n 100
journalctl -u barrier-panel.service -n 100
```

## Частые проблемы

### Web-панель не открывается

Проверьте:

- Raspberry Pi и телефон в одной сети;
- `barrier-panel.service` запущен;
- порт `8080` слушается;
- firewall не блокирует порт.

Команды:

```bash
sudo systemctl status barrier-panel.service
ss -tulpn | grep 8080
hostname -I
```

### Пароль не принимается

Проверьте значение переменной:

```bash
sudo systemctl cat barrier-panel.service
```

После изменения пароля нужен restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart barrier-panel.service
```

### Bluetooth не видит телефон

Проверьте:

- Bluetooth включен на телефоне;
- телефон не спит;
- телефон видим для Bluetooth;
- MAC не меняется из-за приватного адреса;
- `bluetoothctl scan on` реально видит устройства.

Команды:

```bash
bluetoothctl show
timeout 20s bluetoothctl scan on
bluetoothctl devices
```

Если `bluetoothctl show` показывает:

```text
Powered: no
PowerState: off-blocked
```

разблокируйте адаптер и перезапустите сервис:

```bash
sudo rfkill list
sudo rfkill unblock bluetooth
sudo systemctl restart bluetooth
bluetoothctl power on
sudo systemctl restart barrier.service
```

Проверка после исправления:

```bash
bluetoothctl show
journalctl -u barrier.service -n 80 --no-pager
```

### Реле не срабатывает

Проверьте:

- правильный serial-порт;
- права доступа к `/dev/ttyUSB0` или `/dev/ttyACM0`;
- пользователь входит в группу `dialout`;
- реле получает питание;
- команды `relay_on_cmd` и `relay_off_cmd` подходят вашему модулю.

Команды:

```bash
groups
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py --dry-run test-open
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py test-open
```

### BLE-сервис не стартует

Проверьте:

- база инициализирована;
- добавлен хотя бы один enabled MAC;
- Bluetooth работает;
- реле доступно или включен dry-run.

Команды:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py list
sudo systemctl status bluetooth
sudo systemctl status barrier.service
journalctl -u barrier.service -n 100
```

### После перезагрузки ничего не запускается

Проверьте автозапуск:

```bash
sudo systemctl is-enabled bluetooth
sudo systemctl is-enabled barrier.service
sudo systemctl is-enabled barrier-panel.service
```

Включить:

```bash
sudo systemctl enable bluetooth
sudo systemctl enable barrier.service
sudo systemctl enable barrier-panel.service
```

## Безопасность

Минимум для реального использования:

- задать `BARRIER_PANEL_PASSWORD`;
- задать уникальный `BARRIER_FLASK_SECRET_KEY`;
- не открывать порт панели в интернет;
- держать панель только в локальной сети;
- регулярно делать backup базы;
- проверить, что serial-реле не может сработать от случайной команды.

## Что можно улучшить дальше

- Фильтрация по RSSI, чтобы учитывать расстояние до телефона.
- Отдельный аварийный способ открытия.
- Экспорт журнала событий.
- Watchdog здоровья Bluetooth.
- Более строгая авторизация web-панели с пользователями.
- Настройки через отдельный `/etc/barrier/barrier.env`.
