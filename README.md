# Barrier BLE Controller on Raspberry Pi

## Быстрое обновление: конфигурация через env

Основные настройки теперь можно задавать переменными окружения, не меняя Python-код. Пример лежит в `.env.example`.

Часто используемые переменные:

```bash
BARRIER_DB_PATH=/opt/barrier/barrier.db
BARRIER_BACKUP_DIR=/opt/barrier/backups
BARRIER_SCRIPT=/opt/barrier/src/barrier_service.py
BARRIER_RELAY_PORT=/dev/ttyUSB0
BARRIER_RELAY_PORT=auto
BARRIER_DRY_RUN=false
BARRIER_PANEL_PASSWORD=strong-password
BARRIER_PANEL_PORT=8080
```

Новые команды:

```bash
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py detect-relay
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py backup-db
/opt/barrier/venv/bin/python /opt/barrier/src/barrier_service.py --dry-run test-open
```

Если `BARRIER_PANEL_PASSWORD` пустой, web-панель работает без пароля. Если переменная задана, панель требует вход.

Система для управления шлагбаумом на одноплатном компьютере через Bluetooth Low Energy.

Что умеет:

* ищет разрешённые устройства по MAC-адресам;
* хранит список устройств в `SQLite`;
* управляет реле;
* поднимает web-панель для управления с телефона;
* запускается автоматически через `systemd`.

## Архитектура

```text
Телефон (BLE)
      ↓
Raspberry Pi / одноплатник
      ↓
Python-сервис
      ↓
SQLite (список MAC)
      ↓
USB-реле / serial relay
      ↓
Шлагбаум
```

---

# 1. Что должно быть подготовлено

Нужно:

* Raspberry Pi / другой одноплатник с Linux;
* Bluetooth-адаптер;
* Wi-Fi;
* USB-реле или serial-реле;
* Python 3;
* файлы проекта:

  * `barrier_service.py`
  * `panel.py`

Опционально:

* `barrier.service`
* `barrier-panel.service`

---

# 2. Установка системы

## 2.1 Подключение к устройству

```bash
ssh USER@IP_ОДНОПЛАТНИКА
```

## 2.2 Установка системных пакетов

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv bluetooth bluez sqlite3
```

---

# 3. Подготовка рабочей директории

```bash
sudo mkdir -p /opt/barrier
sudo chown -R $USER:$USER /opt/barrier
cd /opt/barrier
```

---

# 4. Копирование файлов проекта

Если копируете с другого компьютера:

```bash
scp barrier_service.py panel.py USER@IP_ОДНОПЛАТНИКА:/opt/barrier/
```

Если есть unit-файлы:

```bash
scp barrier.service barrier-panel.service USER@IP_ОДНОПЛАТНИКА:/opt/barrier/
```

Проверка:

```bash
ls -l /opt/barrier
```

---

# 5. Создание Python virtualenv

```bash
cd /opt/barrier
python3 -m venv venv
source /opt/barrier/venv/bin/activate
pip install --upgrade pip
pip install pyserial flask
```

---

# 6. Права на запуск

```bash
chmod +x /opt/barrier/barrier_service.py
chmod +x /opt/barrier/panel.py
```

---

# 7. Проверка Bluetooth

Включить и запустить системный Bluetooth:

```bash
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
bluetoothctl show
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

---

# 8. Проверка serial-реле

Посмотреть доступные порты:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Если пользователь не имеет доступа к serial-порту:

```bash
sudo usermod -aG dialout $USER
newgrp dialout
```

После этого желательно переподключиться по SSH.

---

# 9. Инициализация базы данных

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py init-db
```

---

# 10. Добавление телефона в базу

Пример:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py add AA:BB:CC:DD:EE:FF "My Phone"
```

Посмотреть список устройств:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py list
```

---

# 11. Как узнать MAC телефона

Можно просканировать Bluetooth-окружение:

```bash
timeout 15s bluetoothctl scan on
bluetoothctl devices
```

Найдите MAC нужного телефона и добавьте его в базу.

---

# 12. Проверка реле

Перед запуском BLE-логики нужно проверить реле отдельно:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py test-open
```

Если реле не сработало, проверьте:

* правильный `relay_port`;
* правильный `relay_baudrate`;
* права доступа к `/dev/ttyUSB0`;
* питание реле;
* команды конкретного релейного модуля.

---

# 13. Ручной запуск основного сервиса

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py run
```

Что делает сервис:

* запускает `bluetoothctl`;
* постоянно отправляет `scan on`;
* получает список найденных устройств;
* сравнивает найденные MAC с базой;
* при обнаружении разрешённого устройства даёт команду на открытие;
* при исчезновении устройства через заданное количество циклов даёт команду на закрытие.

Остановка:

```text
Ctrl+C
```

---

# 14. Ручной запуск web-панели

```bash
/opt/barrier/venv/bin/python /opt/barrier/panel.py
```

Обычно панель доступна на порту `8080`.

---

# 15. Автозапуск web-панели через systemd

Создайте файл:

```bash
sudo nano /etc/systemd/system/barrier-panel.service
```

Содержимое:

```ini
[Unit]
Description=Barrier Web Panel
After=network.target

[Service]
User=ltpibarrier
WorkingDirectory=/opt/barrier
ExecStart=/opt/barrier/venv/bin/python /opt/barrier/panel.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Применить:

```bash
sudo systemctl daemon-reload
sudo systemctl enable barrier-panel.service
sudo systemctl start barrier-panel.service
```

Проверка:

```bash
sudo systemctl status barrier-panel.service
```

Логи:

```bash
journalctl -u barrier-panel.service -f
```

---

# 16. Автозапуск основного BLE-сервиса через systemd

Создайте файл:

```bash
sudo nano /etc/systemd/system/barrier.service
```

Содержимое:

```ini
[Unit]
Description=Barrier BLE Service
After=network.target bluetooth.service
Wants=bluetooth.service

[Service]
User=ltpibarrier
WorkingDirectory=/opt/barrier
ExecStart=/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py run
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Применить:

```bash
sudo systemctl daemon-reload
sudo systemctl enable barrier.service
sudo systemctl start barrier.service
```

Проверка:

```bash
sudo systemctl status barrier.service
```

Логи:

```bash
journalctl -u barrier.service -f
```

---

# 17. Доступ к web-панели

Узнать IP-адрес одноплатника:

```bash
hostname -I
```

Открыть в телефоне:

```text
http://IP_ОДНОПЛАТНИКА:8080
```

Пример:

```text
http://10.44.229.120:8080
```

Важно:

* телефон должен быть в той же Wi-Fi сети, что и одноплатник;
* если web-панель не открывается, проверьте порт `8080`.

---

# 18. Проверка, что web-панель действительно слушает порт

```bash
ss -tulpn | grep 8080
```

Если порт не слушается, смотрите лог сервиса:

```bash
journalctl -u barrier-panel.service -f
```

---

# 19. Минимальный сценарий запуска после установки

Выполните по порядку:

```bash
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py init-db
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py add AA:BB:CC:DD:EE:FF "My Phone"
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py test-open

sudo systemctl daemon-reload
sudo systemctl enable barrier.service barrier-panel.service
sudo systemctl start barrier.service barrier-panel.service
```

После этого:

* BLE-сервис работает автоматически;
* web-панель запускается автоматически;
* доступ с телефона идёт через браузер.

---

# 20. Основные команды управления

Инициализация базы:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py init-db
```

Добавить устройство:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py add AA:BB:CC:DD:EE:FF "Phone"
```

Показать список:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py list
```

Отключить устройство:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py disable AA:BB:CC:DD:EE:FF
```

Включить устройство:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py enable AA:BB:CC:DD:EE:FF
```

Удалить устройство:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py remove AA:BB:CC:DD:EE:FF
```

Тест открытия:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py test-open
```

Ручной запуск:

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py run
```

---

# 21. Быстрая диагностика

## 21.1 Проверка IP

```bash
ip a
hostname -I
```

## 21.2 Проверка Bluetooth

```bash
bluetoothctl show
```

## 21.3 Проверка видимых устройств

```bash
timeout 15s bluetoothctl scan on
bluetoothctl devices
```

## 21.4 Проверка реле

```bash
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py test-open
```

## 21.5 Проверка сервисов

```bash
sudo systemctl status barrier.service
sudo systemctl status barrier-panel.service
```

## 21.6 Логи

```bash
journalctl -u barrier.service -f
journalctl -u barrier-panel.service -f
```

---

# 22. Частые проблемы и решения

## Проблема: web-панель не открывается

Проверьте:

* запущен ли `barrier-panel.service`;
* слушает ли приложение порт `8080`;
* подключён ли телефон к той же Wi-Fi сети.

Команды:

```bash
sudo systemctl status barrier-panel.service
ss -tulpn | grep 8080
hostname -I
```

---

## Проблема: Bluetooth не видит телефон

Проверьте:

* включён ли Bluetooth на телефоне;
* не спит ли телефон;
* не меняется ли MAC из-за рандомизации;
* работает ли `bluetoothctl scan on`.

Команды:

```bash
bluetoothctl show
timeout 20s bluetoothctl scan on
bluetoothctl devices
```

---

## Проблема: сервис не запускается через systemd

Проверьте:

* правильный путь к Python:
  `/opt/barrier/venv/bin/python`
* правильный `WorkingDirectory`;
* права пользователя;
* установлены ли зависимости.

Лог:

```bash
journalctl -u barrier.service -n 100
journalctl -u barrier-panel.service -n 100
```

---

## Проблема: реле не срабатывает

Проверьте:

* верный ли serial-порт;
* верная ли скорость порта;
* есть ли доступ к `ttyUSB`;
* соответствуют ли команды вашему релейному модулю.

Команды:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
groups
```

---

## Проблема: после перезагрузки всё не стартует

Проверьте, что сервисы включены:

```bash
sudo systemctl is-enabled barrier.service
sudo systemctl is-enabled barrier-panel.service
sudo systemctl is-enabled bluetooth
```

Если нет:

```bash
sudo systemctl enable bluetooth
sudo systemctl enable barrier.service
sudo systemctl enable barrier-panel.service
```

---

# 23. Рекомендации для демонстрации

Если нужно показать систему быстро:

* не поднимайте отдельную Wi-Fi точку доступа;
* используйте уже существующую Wi-Fi сеть;
* подключите телефон и Raspberry Pi к одной сети;
* откройте web-панель по текущему IP одноплатника.

Пример:

```text
http://10.44.229.120:8080
```

---

# 24. Рекомендации для продакшена

Желательно добавить:

* watchdog;
* логирование событий в базу;
* фильтрацию по RSSI;
* резервный способ открытия шлагбаума;
* защиту web-панели паролем;
* резервное питание;
* отдельную сервисную кнопку.

---

# 25. Пример полной установки одной последовательностью

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv bluetooth bluez sqlite3

sudo mkdir -p /opt/barrier
sudo chown -R $USER:$USER /opt/barrier

cd /opt/barrier
python3 -m venv venv
source /opt/barrier/venv/bin/activate
pip install --upgrade pip
pip install pyserial flask

chmod +x /opt/barrier/barrier_service.py
chmod +x /opt/barrier/panel.py

sudo systemctl enable bluetooth
sudo systemctl start bluetooth

/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py init-db
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py add AA:BB:CC:DD:EE:FF "My Phone"
/opt/barrier/venv/bin/python /opt/barrier/barrier_service.py test-open
```

Дальше создать два systemd-сервиса из разделов выше и запустить:

```bash
sudo systemctl daemon-reload
sudo systemctl enable barrier.service barrier-panel.service
sudo systemctl start barrier.service barrier-panel.service
```

---

# 26. Что открывать с телефона

1. Подключить телефон к той же Wi-Fi сети.
2. Узнать IP одноплатника:

```bash
hostname -I
```

3. Открыть в браузере:

```text
http://IP_ОДНОПЛАТНИКА:8080
```

