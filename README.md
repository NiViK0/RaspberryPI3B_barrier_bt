# Barrier BLE Controller

Сервис для управления шлагбаумом с Raspberry Pi или другого Linux-одноплатника. Система ищет разрешенные Bluetooth/BLE-устройства и/или подключенные Wi-Fi-устройства по MAC-адресам, хранит список устройств в SQLite, управляет serial-реле и дает web-панель для управления с телефона.

## Возможности

- Поиск разрешенных устройств через `bluetoothctl`.
- Список устройств в SQLite.
- Включение, отключение и удаление устройств через CLI и web-панель.
- Управление USB/serial-реле.
- Dry-run режим для проверки без реального реле.
- Автоопределение serial-порта реле.
- Журнал событий в SQLite.
- Backup базы данных.
- Web-панель со статусом системы, BLE-диагностикой, последними событиями и опциональным паролем.
- BLE-диагностика: время последнего скана, RSSI, количество видимых и подключенных устройств.
- Web-статусы служб и кнопки управления платой.
- Wi-Fi точка доступа для прямого подключения к плате.
- Автооткрытие по Wi-Fi, когда разрешенный телефон подключился к точке доступа платы.
- Wi-Fi-диагностика: подключенные станции, сигнал, неактивность, IP/hostname из DHCP leases.
- Статический Ethernet-адрес для сервисного подключения.
- Автозапуск через `systemd`.
- Unit-тесты логики присутствия устройств.

## Структура проекта

```text
barrier_service.py      CLI и основной сервисный цикл
panel.py                Flask web-панель
barrier_config.py       Конфигурация из переменных окружения
barrier_db.py           SQLite, устройства, события, backup
barrier_bluetooth.py    Работа с bluetoothctl
barrier_wifi.py         Обнаружение подключенных Wi-Fi-станций через iw
barrier_relay.py        Serial-реле, dry-run, автоопределение порта
barrier_presence.py     Логика присутствия устройства
barrier_types.py        Общие типы и состояние
install.sh              Установка на Raspberry Pi/Linux через systemd
scripts/                Сервисные shell-скрипты
.env.example            Пример переменных окружения
tests/                  Unit-тесты
archive/                Старые файлы и production-снимки, не используемые текущей версией
```

В корне лежит только актуальная версия. Старые монолитные скрипты, ранние unit-файлы и прежние production-копии перенесены в `archive/`.

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
        +--> SQLite: allowed_devices, event_log, bluetooth_status, wifi_status
        |
        +--> serial relay
        |
        v
Шлагбаум

Телефон, подключенный к Wi-Fi точки доступа платы
        |
        v
iw dev wlan0 station dump
        |
        v
barrier_service.py

panel.py читает ту же SQLite-базу и вызывает barrier_service.py для команд управления.
```

## Быстрая установка без интернета на плате

Если у Raspberry Pi нет доступа в интернет, но есть SSH/SFTP, устанавливайте проект локальным архивом. Интернет нужен только на компьютере, с которого вы готовите архив и wheel-файлы Python-зависимостей.

На компьютере с проектом:

```bash
cd RaspberryPI3B_barrier_bt
PowerShell -ExecutionPolicy Bypass -File scripts/make_offline_deploy.ps1
```

Передайте на плату по SSH:

```bash
scp deploy/barrier-deploy.tar.gz ltpibarrier@IP_ПЛАТЫ:/tmp/barrier-deploy.tar.gz
scp -r deploy/wheelhouse ltpibarrier@IP_ПЛАТЫ:/tmp/barrier-wheelhouse
```

Если удобнее через SFTP-клиент, передайте:

- `deploy/barrier-deploy.tar.gz` в `/tmp/barrier-deploy.tar.gz`;
- папку `deploy/wheelhouse` в `/tmp/barrier-wheelhouse`.

На Raspberry Pi:

```bash
rm -rf /tmp/barrier-deploy-current
mkdir -p /tmp/barrier-deploy-current
tar -xzf /tmp/barrier-deploy.tar.gz -C /tmp/barrier-deploy-current
find /tmp/barrier-deploy-current -name '*.sh' -exec sed -i 's/\r$//' {} +
sudo \
  INSTALL_FROM_LOCAL=1 \
  LOCAL_SOURCE_DIR=/tmp/barrier-deploy-current \
  PIP_OFFLINE=1 \
  WHEELHOUSE_DIR=/tmp/barrier-wheelhouse \
  bash /tmp/barrier-deploy-current/install.sh
```

Если на плате уже установлены системные пакеты `git`, `python3`, `python3-pip`, `python3-venv`, `bluetooth`, `bluez`, `sqlite3`, `iw`, можно пропустить `apt update` и `apt install`:

```bash
sudo \
  INSTALL_FROM_LOCAL=1 \
  LOCAL_SOURCE_DIR=/tmp/barrier-deploy-current \
  INSTALL_SYSTEM_PACKAGES=0 \
  PIP_OFFLINE=1 \
  WHEELHOUSE_DIR=/tmp/barrier-wheelhouse \
  bash /tmp/barrier-deploy-current/install.sh
```

Для первой установки на полностью чистой плате без интернета системные `.deb`-пакеты нужно поставить заранее офлайн или временно дать плате доступ к apt-репозиториям. Python-зависимости `flask` и `pyserial` ставятся из переданной папки `wheelhouse`.

Если wheel-файлы готовятся на Windows, а плата использует другую архитектуру, pip может отказаться ставить отдельные бинарные зависимости. В таком случае подготовьте `wheelhouse` на Linux/Raspberry Pi той же архитектуры с доступом в интернет и перенесите папку на рабочую плату по SFTP.

Скрипт:

- установит системные пакеты, если не задан `INSTALL_SYSTEM_PACKAGES=0`;
- создаст `/opt/barrier`;
- скопирует исходники в `/opt/barrier/src`;
- создаст virtualenv в `/opt/barrier/venv`;
- установит `pyserial` и `flask`;
- создаст systemd-сервисы;
- создаст Bluetooth watchdog timer;
- установит `/usr/local/bin/barrier-set-time` для синхронизации времени из web-панели;
- настроит ограниченные sudo-права для кнопок управления в web-панели и чтения Wi-Fi-станций через `iw station dump`;
- инициализирует SQLite-базу;
- запустит web-панель;
- попробует запустить основной сервис обнаружения.

Если сервис не стартует сразу, это нормально: сначала может понадобиться добавить MAC-адрес телефона, проверить реле или включить нужный источник обнаружения.

### Установка релиза v1.6.1

Релиз `v1.7.0` добавляет профили устройств и журнал открытий. Теперь Wi-Fi MAC и BLE MAC можно хранить как один профиль телефона, а web-панель показывает историю открытий: источник `wifi`/`ble`/`manual`, профиль, MAC, сигнал и решение `opened`. Это удобно для проверки и доказательства, что шлагбаум открывался именно от нужного устройства.

Релиз `v1.6.1` добавляет удобный runtime-update скрипт `scripts/make_runtime_update.ps1` для обновления уже установленной платы без `pip`, `wheelhouse` и переустановки virtualenv. Также в релиз входит автооткрытие по Wi-Fi из `v1.6.0`: телефон подключается к точке доступа платы, сервис видит разрешенный Wi-Fi MAC через `iw station dump` и отправляет импульс открытия через тот же cooldown, что и BLE. BLE остается включенным по умолчанию, а Wi-Fi-режим включается переменной `BARRIER_WIFI_AUTO_OPEN=true`. SQLite-миграция для `wifi_status` выполняется автоматически через `init-db`.

Если на плате есть интернет:

```bash
git clone --branch v1.6.1 https://github.com/NiViK0/RaspberryPI3B_barrier_bt.git /tmp/barrier
cd /tmp/barrier
sudo bash install.sh
```

На уже установленной системе:

```bash
cd /opt/barrier/src
git fetch --tags origin
git checkout v1.6.1
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py init-db
sudo systemctl restart barrier.service barrier-panel.service
```

После перезапуска откройте web-панель и проверьте блоки `BLE диагностика` и `Wi-Fi диагностика`. В BLE-блоке должны появиться:

- время последнего BLE-скана;
- количество видимых BLE-устройств;
- количество подключенных устройств;
- количество видимых разрешенных устройств;
- лучший RSSI в dBm;
- таблица устройств с MAC, RSSI, connected и allowed.

В Wi-Fi-блоке должны появиться:

- время последнего Wi-Fi-скана;
- интерфейс, обычно `wlan0`;
- количество подключенных станций;
- лучший Wi-Fi-сигнал в dBm;
- таблица станций с IP, MAC, сигналом, неактивностью и флагами `allowed`/`active`.

Для автооткрытия подключите телефон к Wi-Fi сети платы, найдите его MAC в `Wi-Fi диагностика` или через `iw`, добавьте этот MAC в разрешенный список и включите:

```ini
[Service]
Environment=BARRIER_WIFI_AUTO_OPEN=true
Environment=BARRIER_WIFI_INTERFACE=wlan0
Environment=BARRIER_WIFI_MIN_SIGNAL=-75
Environment=BARRIER_WIFI_MAX_INACTIVE_MS=60000
```

Если блок показывает, что сервис еще не записал BLE/Wi-Fi-статус, подождите один цикл сканирования или перезапустите сервис:

```bash
sudo systemctl restart barrier.service
journalctl -u barrier.service -n 80 --no-pager
```

### Установка локальной копии

Если изменения еще не отправлены в GitHub, используйте тот же офлайн-сценарий через SSH/SFTP: соберите архив из текущей папки проекта, передайте его на плату и запустите `install.sh` с `INSTALL_FROM_LOCAL=1`.

```bash
PowerShell -ExecutionPolicy Bypass -File scripts/make_offline_deploy.ps1
scp deploy/barrier-deploy.tar.gz ltpibarrier@IP_ПЛАТЫ:/tmp/barrier-deploy.tar.gz
scp -r deploy/wheelhouse ltpibarrier@IP_ПЛАТЫ:/tmp/barrier-wheelhouse
```

На Raspberry Pi:

```bash
rm -rf /tmp/barrier-deploy-current
mkdir -p /tmp/barrier-deploy-current
tar -xzf /tmp/barrier-deploy.tar.gz -C /tmp/barrier-deploy-current
find /tmp/barrier-deploy-current -name '*.sh' -exec sed -i 's/\r$//' {} +
sudo \
  INSTALL_FROM_LOCAL=1 \
  LOCAL_SOURCE_DIR=/tmp/barrier-deploy-current \
  PIP_OFFLINE=1 \
  WHEELHOUSE_DIR=/tmp/barrier-wheelhouse \
  bash /tmp/barrier-deploy-current/install.sh
```

`INSTALL_FROM_LOCAL=1` отключает клонирование из GitHub и копирует исходники из `LOCAL_SOURCE_DIR` в `/opt/barrier/src`. `PIP_OFFLINE=1` запрещает pip ходить в интернет и ставит зависимости только из `WHEELHOUSE_DIR`.

Если wheel-файлы готовятся на Windows, а плата использует другую архитектуру, pip может отказаться ставить отдельные бинарные зависимости. В таком случае подготовьте `wheelhouse` на Linux/Raspberry Pi той же архитектуры с доступом в интернет и перенесите папку на рабочую плату по SFTP.

### Runtime-обновление без pip

Если плата уже установлена и нужно обновить только код, используйте runtime-архив. Этот сценарий не скачивает Python-зависимости, не собирает `wheelhouse` и не переустанавливает virtualenv.

На компьютере с проектом:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts/make_runtime_update.ps1 -Version v1.6.1 -RemoteHost IP_ПЛАТЫ
scp deploy/barrier-v1.6.1-runtime-update.tar.gz ltpibarrier@IP_ПЛАТЫ:/tmp/barrier-v1.6.1-runtime-update.tar.gz
```

На Raspberry Pi:

```bash
rm -rf /tmp/barrier-v1.6.1-runtime-update
mkdir -p /tmp/barrier-v1.6.1-runtime-update
tar -xzf /tmp/barrier-v1.6.1-runtime-update.tar.gz -C /tmp/barrier-v1.6.1-runtime-update
find /tmp/barrier-v1.6.1-runtime-update -name '*.sh' -exec sed -i 's/\r$//' {} +

sudo cp -a /opt/barrier/src "/opt/barrier/src.backup.$(date +%Y%m%d-%H%M%S)"
sudo cp -a /opt/barrier/barrier.db "/opt/barrier/barrier.db.backup.$(date +%Y%m%d-%H%M%S)"
sudo rsync -a --delete \
  --exclude '.git' \
  --exclude 'deploy' \
  --exclude 'archive' \
  /tmp/barrier-v1.6.1-runtime-update/ \
  /opt/barrier/src/

sudo chown -R ltpibarrier:ltpibarrier /opt/barrier/src
sudo chmod +x /opt/barrier/src/scripts/*.sh
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py init-db
sudo systemctl restart barrier.service barrier-panel.service
```

Если обновление добавляет новые системные пакеты, например `iw` для Wi-Fi-автооткрытия, их нужно поставить отдельно. Runtime-архив обновляет только код и SQLite-схему.

## Переменные окружения

Настройки можно задавать через переменные окружения. Пример есть в `.env.example`.

Основные переменные:

```bash
BARRIER_DB_PATH=/opt/barrier/barrier.db
BARRIER_BACKUP_DIR=/opt/barrier/backups
BARRIER_SCRIPT=/opt/barrier/src/barrier_service.py
BARRIER_EVENT_LOG_LIMIT=2000

BARRIER_RELAY_PORT=/dev/ttyUSB0
BARRIER_RELAY_PORT=auto
BARRIER_RELAY_BAUDRATE=9600
BARRIER_DRY_RUN=false

BARRIER_BLUETOOTH_ENABLED=true
BARRIER_SCAN_TIME=8
BARRIER_CHECK_INTERVAL=2
BARRIER_COOLDOWN=15
BARRIER_PULSE_TIME=2
BARRIER_MISSING_THRESHOLD=3
BARRIER_MIN_RSSI=

BARRIER_WIFI_AUTO_OPEN=false
BARRIER_WIFI_INTERFACE=wlan0
BARRIER_WIFI_MIN_SIGNAL=
BARRIER_WIFI_MAX_INACTIVE_MS=60000
BARRIER_WIFI_LEASES_PATH=/var/lib/misc/dnsmasq.leases

BARRIER_PANEL_HOST=0.0.0.0
BARRIER_PANEL_PORT=8080
BARRIER_PANEL_PASSWORD=replace-with-strong-password
BARRIER_FLASK_SECRET_KEY=replace-with-random-secret
```

`BARRIER_RELAY_PORT=auto` включает поиск первого доступного порта из `/dev/ttyUSB*` и `/dev/ttyACM*`.

`BARRIER_DRY_RUN=true` отключает реальную активацию реле. Сервис будет логировать действия, но не писать в serial-порт.

`BARRIER_MIN_RSSI=-85` включает порог мощности сигнала. Если переменная пустая, любой найденный разрешенный MAC считается присутствующим. Если задан порог, устройство считается присутствующим только когда его RSSI не слабее порога.

`BARRIER_BLUETOOTH_ENABLED=false` отключает BLE-сканирование. Это удобно, если режим нужен только через Wi-Fi.

`BARRIER_WIFI_AUTO_OPEN=true` включает автооткрытие по Wi-Fi. Сервис читает активные станции через `iw dev wlan0 station dump`, сравнивает их MAC с разрешенным списком и использует общий cooldown.

`BARRIER_WIFI_MIN_SIGNAL=-75` включает порог мощности Wi-Fi-сигнала. Если переменная пустая, сигнал не фильтруется.

`BARRIER_WIFI_MAX_INACTIVE_MS=60000` игнорирует Wi-Fi-станции, которые давно не обменивались трафиком. Значение `0` отключает этот фильтр.

`BARRIER_PANEL_PASSWORD` включает пароль для web-панели. Если панель слушает внешний адрес (`0.0.0.0`) и пароль пустой, web-панель блокируется до настройки пароля.

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

Аварийное открытие:

```bash
$PY $APP emergency-open
```

После установки через `install.sh` доступна короткая команда:

```bash
barrier-open
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

Обновить BLE-статус для web-панели вручную:

```bash
$PY $APP scan-status
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

Важно: web-панель запускает команды `barrier_service.py` тем же Python-интерпретатором, под которым запущена сама панель. Поэтому панель нужно запускать через `/opt/barrier/venv/bin/python`, а не через системный `python3`. Иначе кнопки, которые работают с реле, могут не увидеть пакет `pyserial`.

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
- BLE-диагностика: последний скан, RSSI, видимые устройства, connected/allowed;
- возраст последнего BLE-скана и missing-счетчик;
- таблица текущего состояния разрешенных устройств;
- добавление устройства;
- включение и отключение устройства;
- удаление устройства;
- ручное открытие шлагбаума;
- тестовое открытие;
- синхронизация времени платы с временем браузера;
- ручное обновление BLE-скана;
- скачивание диагностического отчета;
- restart Bluetooth;
- backup базы;
- статус системы;
- статус служб `barrier`, `barrier-panel`, `bluetooth`, `watchdog`, `ssh`, `hostapd`, `dnsmasq`, `NetworkManager`;
- кнопки перезапуска BLE-сервиса, Bluetooth и watchdog;
- кнопка перезагрузки платы;
- последние события из SQLite.

Кнопки управления платой используют ограниченный sudoers-файл:

```text
/etc/sudoers.d/barrier-panel-management
```

Он создается `install.sh` и разрешает пользователю сервиса только конкретные команды `systemctl`, необходимые панели управления.

## Время платы без NTP

Если у платы нет интернета, NTP может быть недоступен, и логи будут показывать неправильное время. Для этого в web-панели есть кнопка:

```text
Быстрые действия -> Синхронизировать время
```

Кнопка берет текущее время с телефона или компьютера, где открыта web-панель, и выставляет системное время Raspberry Pi. После установки или обновления убедитесь, что установщик обновил sudoers:

```bash
sudo visudo -cf /etc/sudoers.d/barrier-panel-management
sudo -l -U ltpibarrier | grep barrier-set-time
```

Проверить текущее время платы:

```bash
date
timedatectl status
```

Если кнопка не сработала после обновления старой установки, повторно запустите `install.sh` локальным способом и перезапустите панель:

```bash
sudo systemctl restart barrier-panel.service
```

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

## Точка доступа Wi-Fi

Если рядом нет роутера или нужно подключаться к плате напрямую, Raspberry Pi можно перевести в режим Wi-Fi точки доступа. Тогда смартфон подключается к сети платы и открывает web-панель по фиксированному адресу.

Важно: если Raspberry Pi сейчас подключена к сети через тот же Wi-Fi-интерфейс, включение точки доступа может оборвать текущее Wi-Fi-подключение. Надежнее выполнять настройку через Ethernet, локальную клавиатуру/монитор или другой канал, который не зависит от `wlan0`.

Быстрый вариант:

```bash
cd /opt/barrier/src
sudo AP_PASSWORD='strong-password' bash scripts/setup_wifi_ap.sh
```

По умолчанию скрипт создаст сеть:

```text
SSID: Barrier-Panel
Адрес панели: http://10.42.0.1:8080
```

На смартфоне такая сеть может отображаться как сеть без интернета. Это нормально: она нужна для доступа к web-панели платы.

Настройки можно переопределить:

```bash
sudo \
  AP_INTERFACE=wlan0 \
  AP_SSID='Barrier-Gate' \
  AP_PASSWORD='strong-password' \
  AP_IP=10.42.0.1 \
  bash scripts/setup_wifi_ap.sh
```

После настройки подключи смартфон к этой Wi-Fi сети и открой:

```text
http://10.42.0.1:8080
```

Пароль web-панели все равно лучше включить через `BARRIER_PANEL_PASSWORD`, потому что пароль Wi-Fi защищает только подключение к сети, а не саму кнопку открытия.

Пример настройки пароля web-панели:

```bash
sudo mkdir -p /etc/systemd/system/barrier-panel.service.d
sudo tee /etc/systemd/system/barrier-panel.service.d/override.conf >/dev/null <<'EOF'
[Service]
Environment=BARRIER_PANEL_PASSWORD=strong-panel-password
Environment=BARRIER_FLASK_SECRET_KEY=replace-with-random-string
EOF
sudo systemctl daemon-reload
sudo systemctl restart barrier-panel.service
```

Проверка точки доступа:

```bash
nmcli -t -f NAME,TYPE,DEVICE connection show --active
systemctl is-active barrier-panel.service
curl -I http://127.0.0.1:8080/
```

В активных подключениях должна быть сеть `barrier-ap` на `wlan0`, а web-панель должна отвечать `302` или страницей входа.

## Автооткрытие по Wi-Fi

Режим рассчитан на сценарий: телефон заранее знает Wi-Fi сеть платы, подъезжает к шлагбауму, автоматически подключается к точке доступа, сервис видит разрешенный Wi-Fi MAC и отправляет импульс открытия.

Wi-Fi и BLE можно держать включенными одновременно. В таком режиме Wi-Fi обычно срабатывает раньше, потому что зона покрытия больше: как только разрешенный телефон подключился к точке доступа платы, сервис открывает шлагбаум. Если Wi-Fi еще не поймал телефон, но BLE уже видит разрешенный MAC, BLE тоже может открыть. Оба источника используют общий `BARRIER_COOLDOWN`, поэтому один подъезд не должен давать серию импульсов.

Текущая рабочая схема для платы:

```text
Wi-Fi AP SSID: Barrier-Gate
Wi-Fi AP interface: wlan1
Board Wi-Fi IP: 10.42.1.1
Web panel from AP: http://10.42.1.1:8080
Ethernet/service IP: 10.14.0.117
```

Для ручного открытия через Wi-Fi подключите телефон к `Barrier-Gate`, откройте `http://10.42.1.1:8080`, войдите в web-панель и нажмите `Открыть вручную`.

Порядок настройки:

1. Настройте точку доступа Wi-Fi и подключите к ней телефон.
2. Посмотрите MAC подключенного телефона:

```bash
sudo iw dev wlan0 station dump
```

Если точка доступа работает на внешнем Wi-Fi-адаптере, как в текущей схеме, используйте `wlan1`:

```bash
sudo iw dev wlan1 station dump
```

В выводе нужна строка вида:

```text
Station AA:BB:CC:DD:EE:FF (on wlan1)
```

3. Добавьте этот Wi-Fi MAC в разрешенный список:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py add AA:BB:CC:DD:EE:FF "Phone Wi-Fi"
```

4. Включите автооткрытие в основном сервисе:

```bash
sudo systemctl edit barrier.service
```

```ini
[Service]
Environment=BARRIER_WIFI_AUTO_OPEN=true
Environment=BARRIER_WIFI_INTERFACE=wlan1
Environment=BARRIER_WIFI_MIN_SIGNAL=-85
Environment=BARRIER_WIFI_MAX_INACTIVE_MS=60000
```

`BARRIER_WIFI_MIN_SIGNAL=-85` дает более дальнюю зону срабатывания. Если нужно открывать только совсем рядом с платой, поднимите порог, например до `-75`.

Если хотите режим только через Wi-Fi, без BLE:

```ini
[Service]
Environment=BARRIER_BLUETOOTH_ENABLED=false
Environment=BARRIER_WIFI_AUTO_OPEN=true
Environment=BARRIER_WIFI_INTERFACE=wlan1
```

Для обычного режима с приоритетным Wi-Fi и резервным BLE не задавайте `BARRIER_BLUETOOTH_ENABLED=false`: BLE останется включенным по умолчанию.

5. Чтобы web-панель тоже показывала, что Wi-Fi-автооткрытие включено, добавьте те же Wi-Fi-переменные в `barrier-panel.service`:

```bash
sudo systemctl edit barrier-panel.service
```

6. Примените настройки:

```bash
sudo systemctl daemon-reload
sudo systemctl restart barrier.service barrier-panel.service
```

Проверить вручную:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py wifi-status
journalctl -u barrier.service -n 80 --no-pager
```

Проверить текущие источники и пороги:

```bash
systemctl show barrier.service -p Environment --no-pager
systemctl show barrier-panel.service -p Environment --no-pager
```

В web-панели появится блок `Wi-Fi диагностика`: подключенные станции, MAC, сигнал, неактивность, IP/hostname и флаги `allowed`/`active`.

Важно про телефоны: iOS и Android часто используют приватный MAC для каждой Wi-Fi сети. Обычно он стабилен для конкретной SSID, поэтому добавлять нужно именно MAC из `iw station dump`, когда телефон подключен к сети шлагбаума. Если пользователь сбросит настройки сети или отключит/включит приватный адрес, MAC может измениться.

Чтобы телефон открывал автоматически, его Wi-Fi MAC для сети `Barrier-Gate` должен быть в разрешенном списке:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py list
```

Если телефона нет в списке или MAC отличается от `iw dev wlan1 station dump`, добавьте актуальный MAC:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py add AA:BB:CC:DD:EE:FF "Phone Wi-Fi"
```

В `v1.7.0` удобнее добавлять телефон через web-панель:

1. Подключите телефон к `Barrier-Gate`.
2. Откройте web-панель и нажмите `Обновить Wi-Fi-скан`.
3. В таблице `Wi-Fi диагностика` нажмите `Разрешить` рядом с найденным телефоном.
4. В поле профиля укажите общее имя телефона, например `POCO X7 Pro`.
5. Если BLE MAC тоже виден в `BLE диагностика`, нажмите `Разрешить` рядом с BLE-строкой и укажите тот же профиль.

После этого в списке разрешенных устройств будут две записи с одним профилем:

```text
POCO X7 Pro | wifi | AA:BB:CC:DD:EE:FF
POCO X7 Pro | ble  | 11:22:33:44:55:66
```

Сервис по-прежнему сравнивает конкретные MAC, но для человека это один телефон. При одновременном появлении Wi-Fi и BLE в журнал открытия попадет Wi-Fi, потому что он считается приоритетным источником.

История открытий отображается в web-панели в блоке `История открытий`. Там видно:

- когда был импульс открытия;
- источник: `wifi`, `ble`, `manual`, `test` или `emergency`;
- профиль и MAC устройства;
- RSSI/signal;
- решение, например `opened`.

Практичные стартовые пороги:

- `BARRIER_WIFI_MIN_SIGNAL=-85` для более дальней зоны Wi-Fi-автооткрытия;
- `BARRIER_WIFI_MIN_SIGNAL=-75` для открытия только рядом с платой;
- `BARRIER_WIFI_MAX_INACTIVE_MS=60000`, чтобы не считать давно молчащие станции активными;
- `BARRIER_COOLDOWN=15` или больше, чтобы один подъезд не давал серию импульсов.

## Статический Ethernet

Для сервисного подключения через Ethernet можно закрепить адрес:

```text
IP:      10.14.0.117
Netmask: 255.255.255.0
Gateway: 10.14.0.1
```

Быстрая настройка:

```bash
cd /opt/barrier/src
sudo bash scripts/setup_ethernet_static.sh
```

Скрипт по умолчанию настроит:

```text
Interface: eth0
Address:   10.14.0.117/24
Gateway:   10.14.0.1
```

После настройки web-панель будет доступна через Ethernet:

```text
http://10.14.0.117:8080
```

Настройки можно переопределить:

```bash
sudo \
  ETH_INTERFACE=eth0 \
  ETH_IP=10.14.0.117 \
  ETH_CIDR=24 \
  ETH_GATEWAY=10.14.0.1 \
  ETH_DNS='10.14.0.1 1.1.1.1' \
  bash scripts/setup_ethernet_static.sh
```

Проверка:

```bash
ip addr show eth0
ip route
ping -c 3 10.14.0.1
curl -I http://10.14.0.117:8080/
```

Если подключение выполняется с ноутбука напрямую кабелем без роутера, назначьте ноутбуку адрес из той же сети, например `10.14.0.10/24`.

## SQLite

База по умолчанию:

```text
/opt/barrier/barrier.db
```

Таблицы:

- `allowed_devices`: разрешенные устройства;
- `event_log`: журнал событий.
- `bluetooth_status`: последний BLE-снимок для web-панели.
- `wifi_status`: последний Wi-Fi-снимок для web-панели.

Журнал событий заполняется сервисом, CLI и web-панелью. В него пишутся добавления устройств, включение/отключение, тесты реле, backup, ошибки сканирования и импульсы открытия.

`bluetooth_status` перезаписывается после каждого BLE-скана. Там хранится количество найденных устройств, количество connected-устройств, количество видимых разрешенных MAC, лучший RSSI, самый сильный найденный девайс, JSON со списком устройств, текущий presence-статус, missing-счетчик и RSSI-порог.

`wifi_status` перезаписывается после каждого Wi-Fi-скана. Там хранится интерфейс, подключенные станции, активные разрешенные MAC, лучший сигнал, DHCP IP/hostname, текущий presence-статус, missing-счетчик и Wi-Fi-пороги.

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

## Bluetooth watchdog

Watchdog вынесен в отдельный systemd timer и не нагружает основной Python-сервис.

Что он делает:

- раз в минуту запускает `scripts/bluetooth_watchdog.sh`;
- проверяет `bluetoothctl show`;
- если Bluetooth не в состоянии `Powered: yes` и `PowerState: on`, пробует восстановить адаптер;
- выполняет `rfkill unblock bluetooth`;
- перезапускает `bluetooth.service`;
- включает питание через `bluetoothctl power on`;
- после успешного восстановления перезапускает `barrier.service`.

Проверить timer:

```bash
sudo systemctl status barrier-bluetooth-watchdog.timer
systemctl list-timers | grep barrier-bluetooth-watchdog
```

Запустить watchdog вручную:

```bash
sudo systemctl start barrier-bluetooth-watchdog.service
```

Посмотреть логи:

```bash
journalctl -u barrier-bluetooth-watchdog.service -n 80 --no-pager
```

Отключить watchdog:

```bash
sudo systemctl disable --now barrier-bluetooth-watchdog.timer
```

## Аварийное открытие

Аварийное открытие сделано отдельным способом и не смешано с BLE-логикой основного сервиса.

Доступны два варианта:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py emergency-open
```

и короткая команда после установки:

```bash
barrier-open
```

`barrier-open` устанавливается в `/usr/local/bin/barrier-open` и вызывает:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py emergency-open
```

Событие пишется в SQLite-журнал как:

```text
emergency-open
```

Проверить:

```bash
barrier-open
journalctl -u barrier.service -n 80 --no-pager
```

Если команда нужна пользователю без интерактивного shell-доступа, можно позже добавить отдельное правило `sudoers` или физическую GPIO-кнопку отдельным сервисом.

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

Аварийное открытие:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py emergency-open
barrier-open
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
sudo systemctl enable barrier-bluetooth-watchdog.timer
```

Логи:

```bash
journalctl -u barrier.service -f
journalctl -u barrier-panel.service -f
journalctl -u barrier-bluetooth-watchdog.service -f
```

## Обновление

Если у платы нет интернета и система уже установлена, обновляйте код runtime-архивом. Этот способ не вызывает `pip` и не требует wheel-файлов.

```powershell
cd RaspberryPI3B_barrier_bt
PowerShell -ExecutionPolicy Bypass -File scripts/make_runtime_update.ps1 -Version v1.6.1 -RemoteHost IP_ПЛАТЫ
scp deploy/barrier-v1.6.1-runtime-update.tar.gz ltpibarrier@IP_ПЛАТЫ:/tmp/barrier-v1.6.1-runtime-update.tar.gz
```

На Raspberry Pi:

```bash
rm -rf /tmp/barrier-v1.6.1-runtime-update
mkdir -p /tmp/barrier-v1.6.1-runtime-update
tar -xzf /tmp/barrier-v1.6.1-runtime-update.tar.gz -C /tmp/barrier-v1.6.1-runtime-update
find /tmp/barrier-v1.6.1-runtime-update -name '*.sh' -exec sed -i 's/\r$//' {} +

sudo cp -a /opt/barrier/src "/opt/barrier/src.backup.$(date +%Y%m%d-%H%M%S)"
sudo cp -a /opt/barrier/barrier.db "/opt/barrier/barrier.db.backup.$(date +%Y%m%d-%H%M%S)"
sudo rsync -a --delete --exclude '.git' --exclude 'deploy' --exclude 'archive' /tmp/barrier-v1.6.1-runtime-update/ /opt/barrier/src/
sudo chown -R ltpibarrier:ltpibarrier /opt/barrier/src
sudo chmod +x /opt/barrier/src/scripts/*.sh
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py init-db
sudo systemctl restart barrier.service barrier-panel.service
```

Для первой установки или переустановки virtualenv используйте полный офлайн-сценарий `scripts/make_offline_deploy.ps1` с `wheelhouse`.

Если на плате есть интернет, можно обновить через git:

```bash
cd /opt/barrier/src
git pull origin main
sudo systemctl restart barrier.service barrier-panel.service
```

## Тесты

На машине разработчика:

```bash
python -m unittest discover -s tests
```

Проверка синтаксиса:

```bash
python -m py_compile barrier_config.py barrier_types.py barrier_db.py barrier_presence.py barrier_bluetooth.py barrier_wifi.py barrier_relay.py barrier_service.py panel.py tests/test_presence.py
```

Тесты не требуют Raspberry Pi, Bluetooth, Wi-Fi AP или реле. Они проверяют чистую логику presence-состояний и парсинг диагностических выводов.

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

Проверить Wi-Fi-станции:

```bash
iw dev wlan0 station dump
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py wifi-status
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
sudo systemctl status barrier-bluetooth-watchdog.timer
```

Проверить management-права web-панели:

```bash
sudo visudo -cf /etc/sudoers.d/barrier-panel-management
sudo -l -U ltpibarrier
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

### Кнопка в web-панели пишет, что не установлен pyserial

Такое бывает, если панель запущена системным Python, а зависимости установлены в `/opt/barrier/venv`.

Проверьте unit-файл:

```bash
sudo systemctl cat barrier-panel.service
```

В `ExecStart` должен быть venv Python:

```text
ExecStart=/opt/barrier/venv/bin/python /opt/barrier/src/panel.py
```

Проверьте зависимости:

```bash
/opt/barrier/venv/bin/python -c "import sys; print(sys.executable); import serial; print(serial.__file__)"
```

После обновления кода перезапустите панель:

```bash
cd /opt/barrier/src
git pull origin main
sudo systemctl restart barrier-panel.service
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
- `bluetoothctl scan on` реально видит устройства;
- в web-панели в блоке `BLE диагностика` обновляется время последнего скана.

Команды:

```bash
bluetoothctl show
timeout 20s bluetoothctl scan on
bluetoothctl devices
bluetoothctl info AA:BB:CC:DD:EE:FF
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

Если в web-панели устройство видно, но RSSI пустой, это означает, что `bluetoothctl info MAC` не отдал строку `RSSI`. Для некоторых устройств это нормально: сервис все равно покажет факт обнаружения и статус connected/allowed.

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
sudo systemctl is-enabled barrier-bluetooth-watchdog.timer
```

Включить:

```bash
sudo systemctl enable bluetooth
sudo systemctl enable barrier.service
sudo systemctl enable barrier-panel.service
sudo systemctl enable barrier-bluetooth-watchdog.timer
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
- Экспорт журнала событий.
- Более строгая авторизация web-панели с пользователями.
- Настройки через отдельный `/etc/barrier/barrier.env`.
