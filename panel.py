#!/usr/bin/env python3
import secrets
import subprocess
import sys
import time
from functools import wraps

from flask import Flask, redirect, render_template_string, request, session, url_for

from barrier_config import load_config
from barrier_db import device_counts, init_db, latest_bluetooth_status, list_devices, log_event, recent_events


config = load_config()
app = Flask(__name__)
app.secret_key = config.flask_secret_key

SERVICE_NAMES = [
    "barrier.service",
    "barrier-panel.service",
    "bluetooth.service",
    "barrier-bluetooth-watchdog.timer",
    "ssh.service",
    "hostapd.service",
    "dnsmasq.service",
    "NetworkManager.service",
]

MANAGEMENT_ACTIONS = {
    "restart-barrier": (
        ["sudo", "systemctl", "restart", "barrier.service"],
        "BLE-сервис перезапущен",
    ),
    "restart-bluetooth": (
        ["sudo", "systemctl", "restart", "bluetooth"],
        "Bluetooth перезапущен",
    ),
    "run-watchdog": (
        ["sudo", "systemctl", "start", "barrier-bluetooth-watchdog.service"],
        "Bluetooth watchdog запущен",
    ),
    "restart-watchdog-timer": (
        ["sudo", "systemctl", "restart", "barrier-bluetooth-watchdog.timer"],
        "Bluetooth watchdog timer перезапущен",
    ),
    "reboot-board": (
        ["sudo", "systemctl", "reboot"],
        "Плата уходит в перезагрузку",
    ),
}


HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Barrier Panel</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; max-width: 1100px; }
    h1, h2 { margin-bottom: 10px; }
    .card { border: 1px solid #ccc; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    input[type=password], input[type=text] { width: 100%; padding: 10px; margin: 8px 0; box-sizing: border-box; }
    button { padding: 10px 14px; margin-right: 8px; margin-top: 6px; cursor: pointer; border-radius: 8px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid #ddd; padding: 10px; text-align: left; vertical-align: top; }
    .ok { color: #0a7a0a; }
    .err { color: #b30000; }
    .muted { color: #666; }
    .actions form, .topbar form { display: inline-block; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
    .stat { border-left: 4px solid #555; padding-left: 12px; }
    .stat strong { display: block; font-size: 24px; }
    .service-ok { color: #0a7a0a; font-weight: bold; }
    .service-bad { color: #b30000; font-weight: bold; }
    .ble-ok { border-left-color: #0a7a0a; }
    .ble-warn { border-left-color: #c97700; }
    .ble-bad { border-left-color: #b30000; }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 999px; background: #eee; font-size: 12px; }
    .badge-ok { background: #e6f4e6; color: #0a7a0a; }
    .badge-bad { background: #f7e6e6; color: #b30000; }
    .mono { font-family: Consolas, monospace; }
  </style>
</head>
<body>
  <div class="topbar">
    <h1>Управление шлагбаумом</h1>
    {% if auth_enabled %}
      <form method="post" action="{{ url_for('logout') }}">
        <button type="submit">Выйти</button>
      </form>
    {% endif %}
  </div>

  {% if message %}
    <div class="card {% if success %}ok{% else %}err{% endif %}">{{ message }}</div>
  {% endif %}

  <div class="card">
    <h2>Статус системы</h2>
    <div class="stats">
      <div class="stat"><span class="muted">Устройств</span><strong>{{ total_devices }}</strong></div>
      <div class="stat"><span class="muted">Включено</span><strong>{{ enabled_devices }}</strong></div>
      <div class="stat"><span class="muted">База</span><strong>{{ db_path }}</strong></div>
      <div class="stat"><span class="muted">Реле</span><strong>{{ relay_port }}</strong></div>
      <div class="stat"><span class="muted">IP</span><strong>{{ ip_addresses }}</strong></div>
      <div class="stat"><span class="muted">Время платы</span><strong>{{ board_time }}</strong></div>
    </div>
  </div>

  <div class="card">
    <h2>BLE диагностика</h2>
    {% if bluetooth_status %}
      <div class="stats">
        <div class="stat {% if bluetooth_status.status == 'ok' %}ble-ok{% else %}ble-bad{% endif %}">
          <span class="muted">Последний скан</span>
          <strong>{{ bluetooth_status.updated_at }}</strong>
        </div>
        <div class="stat ble-ok"><span class="muted">Видно BLE</span><strong>{{ bluetooth_status.total_devices }}</strong></div>
        <div class="stat {% if bluetooth_status.connected_devices %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Подключено</span><strong>{{ bluetooth_status.connected_devices }}</strong>
        </div>
        <div class="stat {% if bluetooth_status.allowed_seen %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Разрешенных видно</span><strong>{{ bluetooth_status.allowed_seen }}</strong>
        </div>
        <div class="stat {% if bluetooth_status.max_rssi is not none %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Лучший RSSI</span>
          <strong>{% if bluetooth_status.max_rssi is not none %}{{ bluetooth_status.max_rssi }} dBm{% else %}n/a{% endif %}</strong>
        </div>
      </div>
      {% if bluetooth_status.strongest_device %}
        <p><b>Самый сильный сигнал:</b> {{ bluetooth_status.strongest_device }}</p>
      {% endif %}
      {% if bluetooth_status.error %}
        <p class="err">{{ bluetooth_status.error }}</p>
      {% endif %}
      {% if bluetooth_status.devices %}
        <table>
          <thead>
            <tr>
              <th>Устройство</th>
              <th>MAC</th>
              <th>RSSI</th>
              <th>Подключено</th>
              <th>Допущено</th>
            </tr>
          </thead>
          <tbody>
            {% for d in bluetooth_status.devices %}
              <tr>
                <td>{{ d.name }}</td>
                <td class="mono">{{ d.mac }}</td>
                <td>{% if d.rssi is not none %}{{ d.rssi }} dBm{% else %}<span class="muted">n/a</span>{% endif %}</td>
                <td>{% if d.connected %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge">no</span>{% endif %}</td>
                <td>{% if d.allowed %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge badge-bad">no</span>{% endif %}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      {% else %}
        <p class="muted">BLE-устройства пока не видны.</p>
      {% endif %}
    {% else %}
      <p class="muted">Сервис еще не записал BLE-статус. После следующего скана здесь появятся RSSI и число устройств.</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>Быстрые действия</h2>
    <form method="post" action="{{ url_for('manual_open') }}">
      <button type="submit">Открыть вручную</button>
    </form>
    <form method="post" action="{{ url_for('test_open') }}">
      <button type="submit">Открыть шлагбаум (тест)</button>
    </form>
    <form method="post" action="{{ url_for('restart_bluetooth') }}">
      <button type="submit">Перезапустить Bluetooth</button>
    </form>
    <form id="sync-time-form" method="post" action="{{ url_for('sync_time') }}">
      <input type="hidden" id="sync-time-epoch" name="epoch" value="">
      <button type="submit">Синхронизировать время</button>
    </form>
    <form method="post" action="{{ url_for('backup_db_route') }}">
      <button type="submit">Сделать backup базы</button>
    </form>
  </div>

  <div class="card">
    <h2>Службы</h2>
    <table>
      <thead>
        <tr>
          <th>Служба</th>
          <th>Активность</th>
          <th>Автозапуск</th>
        </tr>
      </thead>
      <tbody>
        {% for service in services %}
          <tr>
            <td>{{ service.name }}</td>
            <td class="{% if service.active == 'active' %}service-ok{% else %}service-bad{% endif %}">{{ service.active }}</td>
            <td>{{ service.enabled }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>Управление платой</h2>
    <form method="post" action="{{ url_for('management_action', action='restart-barrier') }}">
      <button type="submit">Перезапустить BLE-сервис</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='restart-bluetooth') }}">
      <button type="submit">Перезапустить Bluetooth</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='run-watchdog') }}">
      <button type="submit">Запустить Bluetooth watchdog</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='restart-watchdog-timer') }}">
      <button type="submit">Перезапустить watchdog timer</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='reboot-board') }}" onsubmit="return confirm('Перезагрузить плату? Web-панель временно пропадёт.');">
      <button type="submit">Перезагрузить плату</button>
    </form>
  </div>

  <div class="card">
    <h2>Добавить устройство</h2>
    <form method="post" action="{{ url_for('add_device') }}">
      <label>Имя</label>
      <input type="text" name="name" placeholder="Например: Мой телефон" required>
      <label>MAC-адрес</label>
      <input type="text" name="mac" placeholder="AA:BB:CC:DD:EE:FF" required>
      <button type="submit">Добавить</button>
    </form>
  </div>

  <div class="card">
    <h2>Разрешённые устройства</h2>
    {% if devices %}
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Имя</th>
          <th>MAC</th>
          <th>Статус</th>
          <th>Действия</th>
        </tr>
      </thead>
      <tbody>
        {% for d in devices %}
          <tr>
            <td>{{ d[0] }}</td>
            <td>{{ d[1] }}</td>
            <td>{{ d[2] }}</td>
            <td>{% if d[3] %}Включено{% else %}Отключено{% endif %}</td>
            <td class="actions">
              {% if d[3] %}
              <form method="post" action="{{ url_for('disable_device', mac=d[2]) }}">
                <button type="submit">Отключить</button>
              </form>
              {% else %}
              <form method="post" action="{{ url_for('enable_device', mac=d[2]) }}">
                <button type="submit">Включить</button>
              </form>
              {% endif %}
              <form method="post" action="{{ url_for('remove_device', mac=d[2]) }}" onsubmit="return confirm('Удалить устройство?');">
                <button type="submit">Удалить</button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
      <p class="muted">Список устройств пуст.</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>Последние события</h2>
    {% if events %}
    <table>
      <thead>
        <tr>
          <th>Время</th>
          <th>Уровень</th>
          <th>Источник</th>
          <th>Действие</th>
          <th>Сообщение</th>
        </tr>
      </thead>
      <tbody>
        {% for event in events %}
          <tr>
            <td>{{ event[1] }}</td>
            <td>{{ event[2] }}</td>
            <td>{{ event[3] }}</td>
            <td>{{ event[4] }}</td>
            <td>{{ event[5] }}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
      <p class="muted">Событий пока нет.</p>
    {% endif %}
  </div>
  <script>
    document.getElementById('sync-time-form').addEventListener('submit', function () {
      document.getElementById('sync-time-epoch').value = Math.floor(Date.now() / 1000).toString();
    });
  </script>
</body>
</html>
"""


LOGIN_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Barrier Login</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; max-width: 420px; }
    .card { border: 1px solid #ccc; border-radius: 8px; padding: 16px; }
    input[type=password] { width: 100%; padding: 10px; margin: 8px 0; box-sizing: border-box; }
    button { padding: 10px 14px; cursor: pointer; border-radius: 8px; }
    .err { color: #b30000; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Вход</h1>
    {% if error %}<p class="err">{{ error }}</p>{% endif %}
    <form method="post" action="{{ url_for('login') }}">
      <label>Пароль</label>
      <input type="password" name="password" required autofocus>
      <button type="submit">Войти</button>
    </form>
  </div>
</body>
</html>
"""


def auth_enabled() -> bool:
    return bool(config.panel_password)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if auth_enabled() and not session.get("authenticated"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def run_command(cmd: list[str], timeout: int = 30) -> tuple[bool, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


def run_barrier_command(args: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, config.barrier_script] + args
    return run_command(cmd, timeout=90)


def redirect_with_result(ok: bool, output: str, default_message: str = "Готово"):
    return redirect(
        url_for(
            "index",
            message=output or default_message,
            success="1" if ok else "0",
        )
    )


def run_and_redirect(args: list[str], default_message: str = "Готово"):
    ok, output = run_barrier_command(args)
    return redirect_with_result(ok, output, default_message)


def log_panel_event(action: str, message: str, level: str = "INFO") -> None:
    try:
        log_event(config.db_path, level, "panel", action, message)
    except Exception:
        pass


def systemctl_value(service: str, field: str) -> str:
    ok, output = run_command(["systemctl", field, service], timeout=10)
    if ok and output:
        return output
    return output or "unknown"


def service_statuses() -> list[dict[str, str]]:
    return [
        {
            "name": service,
            "active": systemctl_value(service, "is-active"),
            "enabled": systemctl_value(service, "is-enabled"),
        }
        for service in SERVICE_NAMES
    ]


def ip_addresses() -> str:
    ok, output = run_command(["hostname", "-I"], timeout=5)
    if ok and output:
        return output
    return "unknown"


def board_time() -> str:
    ok, output = run_command(["date", "+%Y-%m-%d %H:%M:%S %Z"], timeout=5)
    if ok and output:
        return output
    return time.strftime("%Y-%m-%d %H:%M:%S")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("index"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, config.panel_password):
            session["authenticated"] = True
            log_panel_event("login", "Вход в web-панель")
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="Неверный пароль")

    return render_template_string(LOGIN_HTML, error="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    init_db(config.db_path)
    message = request.args.get("message", "")
    success = request.args.get("success", "1") == "1"
    total_devices, enabled_devices = device_counts(config.db_path)
    return render_template_string(
        HTML,
        auth_enabled=auth_enabled(),
        devices=list_devices(config.db_path),
        events=recent_events(config.db_path, 20),
        message=message,
        success=success,
        total_devices=total_devices,
        enabled_devices=enabled_devices,
        db_path=config.db_path,
        relay_port=config.relay_port,
        ip_addresses=ip_addresses(),
        board_time=board_time(),
        services=service_statuses(),
        bluetooth_status=latest_bluetooth_status(config.db_path),
    )


@app.route("/add", methods=["POST"])
@login_required
def add_device():
    name = request.form.get("name", "").strip()
    mac = request.form.get("mac", "").strip()
    log_panel_event("device-add-request", f"Запрос добавления: {name} [{mac}]")
    return run_and_redirect(["add", mac, name])


@app.route("/enable/<mac>", methods=["POST"])
@login_required
def enable_device(mac: str):
    log_panel_event("device-enable-request", f"Запрос включения: {mac}")
    return run_and_redirect(["enable", mac])


@app.route("/disable/<mac>", methods=["POST"])
@login_required
def disable_device(mac: str):
    log_panel_event("device-disable-request", f"Запрос отключения: {mac}")
    return run_and_redirect(["disable", mac])


@app.route("/remove/<mac>", methods=["POST"])
@login_required
def remove_device(mac: str):
    log_panel_event("device-remove-request", f"Запрос удаления: {mac}")
    return run_and_redirect(["remove", mac])


@app.route("/test-open", methods=["POST"])
@login_required
def test_open():
    log_panel_event("relay-test-request", "Запрос тестового открытия")
    return run_and_redirect(["test-open"])


@app.route("/manual-open", methods=["POST"])
@login_required
def manual_open():
    log_panel_event("manual-open-request", "Запрос ручного открытия")
    return run_and_redirect(["manual-open"], "Шлагбаум открыт вручную")


@app.route("/backup-db", methods=["POST"])
@login_required
def backup_db_route():
    log_panel_event("backup-db-request", "Запрос backup базы")
    return run_and_redirect(["backup-db"])


@app.route("/sync-time", methods=["POST"])
@login_required
def sync_time():
    epoch_raw = request.form.get("epoch", "").strip()
    try:
        epoch = int(epoch_raw)
    except ValueError:
        return redirect_with_result(False, "Некорректное время браузера")

    min_epoch = 1704067200  # 2024-01-01T00:00:00Z
    max_epoch = 1893456000  # 2030-01-01T00:00:00Z
    if epoch < min_epoch or epoch > max_epoch:
        return redirect_with_result(False, f"Подозрительное время браузера: {epoch}")

    ok, output = run_command(["sudo", "/usr/local/bin/barrier-set-time", str(epoch)], timeout=15)
    message = output or "Время платы синхронизировано с браузером"
    log_panel_event("sync-time", message, "INFO" if ok else "ERROR")
    return redirect_with_result(ok, message)


@app.route("/management/<action>", methods=["POST"])
@login_required
def management_action(action: str):
    if action not in MANAGEMENT_ACTIONS:
        return redirect_with_result(False, "Неизвестное действие управления")

    cmd, default_message = MANAGEMENT_ACTIONS[action]
    ok, output = run_command(cmd, timeout=60)
    log_panel_event(action, output or default_message, "INFO" if ok else "ERROR")
    return redirect_with_result(ok, output, default_message)


@app.route("/restart-bluetooth", methods=["POST"])
@login_required
def restart_bluetooth():
    cmd, default_message = MANAGEMENT_ACTIONS["restart-bluetooth"]
    ok, output = run_command(cmd, timeout=60)
    log_panel_event("restart-bluetooth", output.strip() or "Bluetooth перезапущен", "INFO" if ok else "ERROR")
    return redirect_with_result(ok, output.strip(), default_message)


if __name__ == "__main__":
    app.run(host=config.host, port=config.port, debug=False)
