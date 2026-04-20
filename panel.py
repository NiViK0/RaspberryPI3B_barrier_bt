#!/usr/bin/env python3
import secrets
import subprocess
from functools import wraps

from flask import Flask, redirect, render_template_string, request, session, url_for

from barrier_config import load_config
from barrier_db import device_counts, init_db, list_devices, log_event, recent_events


config = load_config()
app = Flask(__name__)
app.secret_key = config.flask_secret_key


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
    </div>
  </div>

  <div class="card">
    <h2>Быстрые действия</h2>
    <form method="post" action="{{ url_for('test_open') }}">
      <button type="submit">Открыть шлагбаум (тест)</button>
    </form>
    <form method="post" action="{{ url_for('restart_bluetooth') }}">
      <button type="submit">Перезапустить Bluetooth</button>
    </form>
    <form method="post" action="{{ url_for('backup_db_route') }}">
      <button type="submit">Сделать backup базы</button>
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


def run_barrier_command(args: list[str]) -> tuple[bool, str]:
    cmd = ["python3", config.barrier_script] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


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


@app.route("/backup-db", methods=["POST"])
@login_required
def backup_db_route():
    log_panel_event("backup-db-request", "Запрос backup базы")
    return run_and_redirect(["backup-db"])


@app.route("/restart-bluetooth", methods=["POST"])
@login_required
def restart_bluetooth():
    cmd = ["bash", "-lc", "sudo systemctl restart bluetooth"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    output = (result.stdout or "") + (result.stderr or "")
    ok = result.returncode == 0
    log_panel_event("restart-bluetooth", output.strip() or "Bluetooth перезапущен", "INFO" if ok else "ERROR")
    return redirect_with_result(ok, output.strip(), "Bluetooth перезапущен")


if __name__ == "__main__":
    app.run(host=config.host, port=config.port, debug=False)
