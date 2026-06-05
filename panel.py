#!/usr/bin/env python3
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, Response, abort, has_request_context, jsonify, redirect, render_template_string, request, session, url_for

from barrier_config import load_config
from barrier_db import (
    device_counts,
    init_db,
    latest_bluetooth_status,
    latest_wifi_status,
    list_devices,
    log_event,
    normalize_mac,
    recent_events,
)


config = load_config()
app = Flask(__name__)
app.secret_key = config.flask_secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

UNSAFE_SECRET_KEYS = {"", "change-me", "barrier-panel-local-secret"}
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCKOUT_SECONDS = 300

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
    .small { font-size: 13px; }
  </style>
</head>
<body>
  <div class="topbar">
    <h1>Управление шлагбаумом</h1>
    {% if auth_enabled %}
      <form method="post" action="{{ url_for('logout') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
        <button type="submit">Выйти</button>
      </form>
    {% endif %}
  </div>

  {% if message %}
    <div class="card {% if success %}ok{% else %}err{% endif %}">{{ message }}</div>
  {% endif %}

  {% if security_warnings %}
    <div class="card err">
      <h2>Security</h2>
      {% for warning in security_warnings %}
        <p>{{ warning }}</p>
      {% endfor %}
    </div>
  {% endif %}

  <div class="card">
    <h2>Статус системы</h2>
    <div class="stats">
      <div class="stat"><span class="muted">Устройств</span><strong>{{ total_devices }}</strong></div>
      <div class="stat"><span class="muted">Включено</span><strong>{{ enabled_devices }}</strong></div>
      <div class="stat"><span class="muted">База</span><strong>{{ db_path }}</strong></div>
      <div class="stat"><span class="muted">Реле</span><strong>{{ relay_port }}</strong></div>
      <div class="stat"><span class="muted">IP</span><strong>{{ ip_addresses }}</strong></div>
      <div class="stat"><span class="muted">Время платы</span><strong id="board-time" data-epoch="{{ board_time_epoch }}">{{ board_time }}</strong><span class="muted small" id="time-drift"></span></div>
    </div>
  </div>

  <div class="card">
    <h2>BLE диагностика</h2>
    {% if bluetooth_status %}
      <div class="stats">
        <div class="stat {% if bluetooth_status.status == 'ok' %}ble-ok{% else %}ble-bad{% endif %}">
          <span class="muted">Последний скан</span>
          <strong>{{ bluetooth_status.updated_at }}</strong>
          <span class="muted small">{{ bluetooth_status.age_label }}</span>
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
        <div class="stat {% if bluetooth_status.allowed_present %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Presence</span>
          <strong>{{ bluetooth_status.presence_status }}</strong>
          <span class="muted small">missing {{ bluetooth_status.missing_count }}/{{ bluetooth_status.missing_threshold }}</span>
        </div>
      </div>
      {% if bluetooth_status.min_rssi is not none %}
        <p><b>RSSI-порог:</b> {{ bluetooth_status.min_rssi }} dBm</p>
      {% endif %}
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
    <h2>Wi-Fi диагностика</h2>
    {% if wifi_status %}
      <div class="stats">
        <div class="stat {% if wifi_status.status == 'ok' %}ble-ok{% else %}ble-bad{% endif %}">
          <span class="muted">Последний скан</span>
          <strong>{{ wifi_status.updated_at }}</strong>
          <span class="muted small">{{ wifi_status.age_label }}</span>
        </div>
        <div class="stat"><span class="muted">Интерфейс</span><strong>{{ wifi_status.interface }}</strong></div>
        <div class="stat {% if wifi_status.connected_devices %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Подключено</span><strong>{{ wifi_status.connected_devices }}</strong>
        </div>
        <div class="stat {% if wifi_status.allowed_seen %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Разрешенных активно</span><strong>{{ wifi_status.allowed_seen }}</strong>
        </div>
        <div class="stat {% if wifi_status.max_signal is not none %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Лучший сигнал</span>
          <strong>{% if wifi_status.max_signal is not none %}{{ wifi_status.max_signal }} dBm{% else %}n/a{% endif %}</strong>
        </div>
        <div class="stat {% if wifi_status.allowed_present %}ble-ok{% else %}ble-warn{% endif %}">
          <span class="muted">Presence</span>
          <strong>{{ wifi_status.presence_status }}</strong>
          <span class="muted small">missing {{ wifi_status.missing_count }}/{{ wifi_status.missing_threshold }}</span>
        </div>
      </div>
      <p><b>Автооткрытие Wi-Fi:</b> {% if wifi_auto_open %}<span class="badge badge-ok">on</span>{% else %}<span class="badge">off</span>{% endif %}</p>
      {% if wifi_status.min_signal is not none %}
        <p><b>Порог сигнала:</b> {{ wifi_status.min_signal }} dBm</p>
      {% endif %}
      {% if wifi_status.max_inactive_ms %}
        <p><b>Макс. неактивность:</b> {{ wifi_status.max_inactive_ms }} ms</p>
      {% endif %}
      {% if wifi_status.strongest_station %}
        <p><b>Самый сильный сигнал:</b> {{ wifi_status.strongest_station }}</p>
      {% endif %}
      {% if wifi_status.error %}
        <p class="err">{{ wifi_status.error }}</p>
      {% endif %}
      {% if wifi_status.stations %}
        <table>
          <thead>
            <tr>
              <th>Устройство</th>
              <th>IP</th>
              <th>MAC</th>
              <th>Сигнал</th>
              <th>Неактивность</th>
              <th>Допущено</th>
              <th>Активно</th>
            </tr>
          </thead>
          <tbody>
            {% for s in wifi_status.stations %}
              <tr>
                <td>{{ s.name }}</td>
                <td>{% if s.ip %}{{ s.ip }}{% else %}<span class="muted">n/a</span>{% endif %}</td>
                <td class="mono">{{ s.mac }}</td>
                <td>{% if s.signal is not none %}{{ s.signal }} dBm{% else %}<span class="muted">n/a</span>{% endif %}</td>
                <td>{% if s.inactive_ms is not none %}{{ s.inactive_ms }} ms{% else %}<span class="muted">n/a</span>{% endif %}</td>
                <td>{% if s.allowed %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge badge-bad">no</span>{% endif %}</td>
                <td>{% if s.active %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge">no</span>{% endif %}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      {% else %}
        <p class="muted">Wi-Fi станции пока не подключены.</p>
      {% endif %}
    {% else %}
      <p class="muted">Сервис еще не записал Wi-Fi-статус. Включи автооткрытие или запусти обновление Wi-Fi-скана.</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>Разрешенные устройства сейчас</h2>
    {% if allowed_statuses %}
      <table>
        <thead>
          <tr>
            <th>Имя</th>
            <th>MAC</th>
            <th>Источник</th>
            <th>Состояние</th>
            <th>Сигнал</th>
            <th>Connected</th>
            <th>Active</th>
            <th>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {% for d in allowed_statuses %}
            <tr>
              <td>{{ d.name }}</td>
              <td class="mono">{{ d.mac }}</td>
              <td>{% if d.source %}{{ d.source }}{% else %}<span class="muted">n/a</span>{% endif %}</td>
              <td>{% if d.seen %}<span class="badge badge-ok">видно</span>{% else %}<span class="badge badge-bad">не видно</span>{% endif %}</td>
              <td>{% if d.signal is not none %}{{ d.signal }} dBm{% else %}<span class="muted">n/a</span>{% endif %}</td>
              <td>{% if d.connected %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge">no</span>{% endif %}</td>
              <td>{% if d.active %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge">no</span>{% endif %}</td>
              <td>{% if d.enabled %}<span class="badge badge-ok">yes</span>{% else %}<span class="badge">no</span>{% endif %}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p class="muted">Разрешенные устройства пока не добавлены.</p>
    {% endif %}
  </div>

  <div class="card">
    <h2>Быстрые действия</h2>
    <form method="post" action="{{ url_for('manual_open') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Открыть вручную</button>
    </form>
    <form method="post" action="{{ url_for('test_open') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Открыть шлагбаум (тест)</button>
    </form>
    <form method="post" action="{{ url_for('restart_bluetooth') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Перезапустить Bluetooth</button>
    </form>
    <form method="post" action="{{ url_for('refresh_ble_status') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Обновить BLE-скан</button>
    </form>
    <form method="post" action="{{ url_for('refresh_wifi_status') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Обновить Wi-Fi-скан</button>
    </form>
    <form id="sync-time-form" method="post" action="{{ url_for('sync_time') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <input type="hidden" id="sync-time-epoch" name="epoch" value="">
      <button type="submit">Синхронизировать время</button>
    </form>
    <form method="post" action="{{ url_for('backup_db_route') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Сделать backup базы</button>
    </form>
    <form method="get" action="{{ url_for('diagnostic_report') }}">
      <button type="submit">Скачать диагностику</button>
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
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Перезапустить BLE-сервис</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='restart-bluetooth') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Перезапустить Bluetooth</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='run-watchdog') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Запустить Bluetooth watchdog</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='restart-watchdog-timer') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Перезапустить watchdog timer</button>
    </form>
    <form method="post" action="{{ url_for('management_action', action='reboot-board') }}" onsubmit="return confirm('Перезагрузить плату? Web-панель временно пропадёт.');">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit">Перезагрузить плату</button>
    </form>
  </div>

  <div class="card">
    <h2>Добавить устройство</h2>
    <form method="post" action="{{ url_for('add_device') }}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
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
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <button type="submit">Отключить</button>
              </form>
              {% else %}
              <form method="post" action="{{ url_for('enable_device', mac=d[2]) }}">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                <button type="submit">Включить</button>
              </form>
              {% endif %}
              <form method="post" action="{{ url_for('remove_device', mac=d[2]) }}" onsubmit="return confirm('Удалить устройство?');">
                <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
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
    (function () {
      var board = document.getElementById('board-time');
      var drift = document.getElementById('time-drift');
      if (!board || !drift) return;
      var boardEpoch = parseInt(board.dataset.epoch || '0', 10);
      if (!boardEpoch) return;
      var diff = Math.round(Date.now() / 1000) - boardEpoch;
      var abs = Math.abs(diff);
      drift.textContent = 'браузер: ' + (diff >= 0 ? '+' : '-') + abs + ' сек';
      if (abs > 30) drift.className = 'err small';
    })();
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
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <label>Пароль</label>
      <input type="password" name="password" required autofocus>
      <button type="submit">Войти</button>
    </form>
  </div>
</body>
</html>
"""


SECURITY_SETUP_HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Barrier Panel Security</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; max-width: 720px; }
    .card { border: 1px solid #ccc; border-radius: 8px; padding: 16px; }
    code { background: #f3f3f3; padding: 2px 5px; border-radius: 4px; }
    .err { color: #b30000; }
  </style>
</head>
<body>
  <div class="card">
    <h1 class="err">Web-панель заблокирована</h1>
    <p>Панель слушает внешний адрес, но <code>BARRIER_PANEL_PASSWORD</code> не задан.</p>
    <p>Задайте пароль и уникальный <code>BARRIER_FLASK_SECRET_KEY</code> в override для <code>barrier-panel.service</code>, затем перезапустите сервис.</p>
  </div>
</body>
</html>
"""


def auth_enabled() -> bool:
    return bool(config.panel_password)


def panel_password_required() -> bool:
    return config.host not in {"127.0.0.1", "localhost", "::1"}


def panel_locked_by_missing_password() -> bool:
    return panel_password_required() and not auth_enabled()


def insecure_secret_configured() -> bool:
    return config.flask_secret_key in UNSAFE_SECRET_KEYS


def request_origin() -> str:
    return request.remote_addr or "unknown"


def get_csrf_token() -> str:
    token = session.get("csrf_token")
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf_token() -> bool:
    expected = session.get("csrf_token")
    supplied = request.form.get("csrf_token", "")
    return isinstance(expected, str) and secrets.compare_digest(expected, supplied)


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": get_csrf_token}


@app.before_request
def protect_post_requests():
    if request.method == "POST" and not validate_csrf_token():
        log_panel_event("csrf-failed", f"CSRF check failed from {request_origin()}", "WARN")
        abort(400)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if panel_locked_by_missing_password():
            return render_template_string(SECURITY_SETUP_HTML), 503
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
        if has_request_context():
            message = f"{message} | remote={request_origin()}"
        log_event(config.db_path, level, "panel", action, message)
    except Exception:
        pass


def security_warnings() -> list[str]:
    warnings = []
    if not auth_enabled():
        warnings.append("BARRIER_PANEL_PASSWORD is not set.")
    if insecure_secret_configured():
        warnings.append("BARRIER_FLASK_SECRET_KEY is missing or uses a default value.")
    return warnings


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


def status_with_age(status: dict[str, object] | None) -> dict[str, object] | None:
    if status is None:
        return None

    updated_at = str(status.get("updated_at") or "")
    age_seconds = None
    try:
        updated_dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((datetime.now(timezone.utc) - updated_dt).total_seconds()))
    except ValueError:
        pass

    if age_seconds is None:
        age_label = "возраст неизвестен"
    elif age_seconds < 60:
        age_label = f"{age_seconds} сек назад"
    else:
        age_label = f"{age_seconds // 60} мин назад"

    status["age_seconds"] = age_seconds
    status["age_label"] = age_label
    return status


def bluetooth_status_for_view() -> dict[str, object] | None:
    return status_with_age(latest_bluetooth_status(config.db_path))


def wifi_status_for_view() -> dict[str, object] | None:
    return status_with_age(latest_wifi_status(config.db_path))


def allowed_device_statuses(
    devices: list[tuple],
    bluetooth_status: dict[str, object] | None,
    wifi_status: dict[str, object] | None,
) -> list[dict[str, object]]:
    visible_by_mac: dict[str, dict[str, object]] = {}
    if bluetooth_status:
        for device in bluetooth_status.get("devices", []):
            if isinstance(device, dict) and device.get("mac"):
                visible_by_mac[normalize_mac(str(device["mac"]))] = {
                    "source": "BLE",
                    "signal": device.get("rssi"),
                    "connected": bool(device.get("connected")),
                    "active": bool(device.get("allowed")),
                }
    if wifi_status:
        for station in wifi_status.get("stations", []):
            if isinstance(station, dict) and station.get("mac"):
                mac = normalize_mac(str(station["mac"]))
                if mac not in visible_by_mac or station.get("active"):
                    visible_by_mac[mac] = {
                        "source": "Wi-Fi",
                        "signal": station.get("signal"),
                        "connected": True,
                        "active": bool(station.get("active")),
                    }

    rows = []
    for _row_id, name, mac, enabled in devices:
        normalized_mac = normalize_mac(mac)
        visible = visible_by_mac.get(normalized_mac)
        rows.append(
            {
                "name": name,
                "mac": normalized_mac,
                "enabled": bool(enabled),
                "seen": visible is not None,
                "signal": visible.get("signal") if visible else None,
                "connected": bool(visible.get("connected")) if visible else False,
                "source": visible.get("source") if visible else "",
                "active": bool(visible.get("active")) if visible else False,
            }
        )
    return rows


@app.route("/login", methods=["GET", "POST"])
def login():
    if panel_locked_by_missing_password():
        return render_template_string(SECURITY_SETUP_HTML), 503
    if not auth_enabled():
        return redirect(url_for("index"))

    if request.method == "POST":
        now = int(time.time())
        locked_until = int(session.get("login_locked_until", 0) or 0)
        if locked_until > now:
            log_panel_event("login-locked", "Login attempt rejected during lockout", "WARN")
            return render_template_string(LOGIN_HTML, error="Слишком много попыток. Попробуйте позже"), 429

        password = request.form.get("password", "")
        if secrets.compare_digest(password, config.panel_password):
            session["authenticated"] = True
            session.pop("login_attempts", None)
            session.pop("login_locked_until", None)
            log_panel_event("login", "Вход в web-панель")
            return redirect(url_for("index"))

        attempts = int(session.get("login_attempts", 0) or 0) + 1
        session["login_attempts"] = attempts
        if attempts >= LOGIN_ATTEMPT_LIMIT:
            session["login_locked_until"] = now + LOGIN_LOCKOUT_SECONDS
            session["login_attempts"] = 0
            log_panel_event("login-lockout", "Too many invalid login attempts", "WARN")
            return render_template_string(LOGIN_HTML, error="Слишком много попыток. Попробуйте позже"), 429

        log_panel_event("login-failed", "Invalid panel password", "WARN")
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
    devices = list_devices(config.db_path)
    bluetooth_status = bluetooth_status_for_view()
    wifi_status = wifi_status_for_view()
    return render_template_string(
        HTML,
        auth_enabled=auth_enabled(),
        devices=devices,
        allowed_statuses=allowed_device_statuses(devices, bluetooth_status, wifi_status),
        events=recent_events(config.db_path, 20),
        message=message,
        success=success,
        total_devices=total_devices,
        enabled_devices=enabled_devices,
        db_path=config.db_path,
        relay_port=config.relay_port,
        ip_addresses=ip_addresses(),
        board_time=board_time(),
        board_time_epoch=int(time.time()),
        services=service_statuses(),
        bluetooth_status=bluetooth_status,
        wifi_status=wifi_status,
        wifi_auto_open=config.wifi_auto_open,
        security_warnings=security_warnings(),
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


@app.route("/refresh-ble", methods=["POST"])
@login_required
def refresh_ble_status():
    log_panel_event("refresh-ble-request", "Запрос ручного BLE-скана")
    return run_and_redirect(["scan-status"], "BLE-статус обновлен")


@app.route("/refresh-wifi", methods=["POST"])
@login_required
def refresh_wifi_status():
    log_panel_event("refresh-wifi-request", "Запрос ручного Wi-Fi-скана")
    return run_and_redirect(["wifi-status"], "Wi-Fi-статус обновлен")


@app.route("/diagnostic-report", methods=["GET"])
@login_required
def diagnostic_report():
    init_db(config.db_path)
    checks = [
        ("date", ["date"]),
        ("timedatectl", ["timedatectl", "status"]),
        ("hostname -I", ["hostname", "-I"]),
        ("iw station dump", ["iw", "dev", config.wifi_interface, "station", "dump"]),
        ("bluetoothctl show", ["bluetoothctl", "show"]),
        ("barrier.service", ["systemctl", "status", "barrier.service", "--no-pager"]),
        ("barrier-panel.service", ["systemctl", "status", "barrier-panel.service", "--no-pager"]),
        ("watchdog timer", ["systemctl", "status", "barrier-bluetooth-watchdog.timer", "--no-pager"]),
    ]

    lines = ["Barrier diagnostic report", f"Generated at: {board_time()}", ""]
    lines.append("Allowed devices:")
    for row in list_devices(config.db_path):
        lines.append(f"- {row[1]} | {row[2]} | {'enabled' if row[3] else 'disabled'}")

    lines.append("")
    lines.append("Latest BLE status:")
    status = bluetooth_status_for_view()
    lines.append(str(status or "No BLE status yet"))

    lines.append("")
    lines.append("Latest Wi-Fi status:")
    wifi_status = wifi_status_for_view()
    lines.append(str(wifi_status or "No Wi-Fi status yet"))

    for title, cmd in checks:
        ok, output = run_command(cmd, timeout=15)
        lines.extend(["", f"## {title}", f"ok={ok}", output or "(no output)"])

    lines.append("")
    lines.append("Security warnings:")
    for warning in security_warnings():
        lines.append(f"- {warning}")
    if not security_warnings():
        lines.append("- none")

    lines.append("")
    lines.append("Recent events:")
    for event in recent_events(config.db_path, 30):
        lines.append(f"{event[1]} | {event[2]} | {event[3]} | {event[4]} | {event[5]}")

    body = "\n".join(lines) + "\n"
    return Response(
        body,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=barrier-diagnostic.txt"},
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    try:
        init_db(config.db_path)
        bluetooth_status = latest_bluetooth_status(config.db_path)
        wifi_status = latest_wifi_status(config.db_path)
        warnings = security_warnings()
        payload = {
            "ok": not warnings,
            "db": "ok",
            "bluetooth_status": bluetooth_status.get("status") if bluetooth_status else "missing",
            "wifi_status": wifi_status.get("status") if wifi_status else "missing",
            "security_warnings": warnings,
        }
        return jsonify(payload), 200 if payload["ok"] else 503
    except Exception as exc:
        return jsonify({"ok": False, "db": "error", "error": str(exc)}), 503


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
