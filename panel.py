#!/usr/bin/env python3
import sqlite3
import subprocess
from dataclasses import dataclass

from flask import Flask, redirect, render_template_string, request, url_for


@dataclass(frozen=True)
class Config:
    db_path: str = "/opt/barrier/barrier.db"
    barrier_script: str = "/opt/barrier/barrier_service.py"
    host: str = "0.0.0.0"
    port: int = 8080


app = Flask(__name__)
config = Config()


HTML = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Barrier Panel</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; max-width: 900px; }
    h1, h2 { margin-bottom: 10px; }
    .card { border: 1px solid #ccc; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
    input[type=text] { width: 100%; padding: 10px; margin: 8px 0; box-sizing: border-box; }
    button { padding: 10px 14px; margin-right: 8px; margin-top: 6px; cursor: pointer; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid #ddd; padding: 10px; text-align: left; }
    .ok { color: #0a7a0a; }
    .err { color: #b30000; }
    .muted { color: #666; }
    .actions form { display: inline-block; }
  </style>
</head>
<body>
  <h1>Управление шлагбаумом</h1>

  {% if message %}
    <div class="card {% if success %}ok{% else %}err{% endif %}">{{ message }}</div>
  {% endif %}

  <div class="card">
    <h2>Быстрые действия</h2>
    <form method="post" action="{{ url_for('test_open') }}">
      <button type="submit">Открыть шлагбаум (тест)</button>
    </form>
    <form method="post" action="{{ url_for('restart_bluetooth') }}">
      <button type="submit">Перезапустить Bluetooth</button>
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
            <td>{% if d[3] %}enabled{% else %}disabled{% endif %}</td>
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
</body>
</html>
"""


def db_rows() -> list[tuple[int, str, str, int]]:
    with sqlite3.connect(config.db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, mac, enabled FROM allowed_devices ORDER BY name"
        ).fetchall()
    return rows


def run_barrier_command(args: list[str]) -> tuple[bool, str]:
    cmd = ["python3", config.barrier_script] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


@app.route("/")
def index():
    message = request.args.get("message", "")
    success = request.args.get("success", "1") == "1"
    return render_template_string(HTML, devices=db_rows(), message=message, success=success)


@app.route("/add", methods=["POST"])
def add_device():
    name = request.form.get("name", "").strip()
    mac = request.form.get("mac", "").strip()
    ok, output = run_barrier_command(["add", mac, name])
    return redirect(url_for("index", message=output or "Готово", success="1" if ok else "0"))


@app.route("/enable/<mac>", methods=["POST"])
def enable_device(mac: str):
    ok, output = run_barrier_command(["enable", mac])
    return redirect(url_for("index", message=output or "Готово", success="1" if ok else "0"))


@app.route("/disable/<mac>", methods=["POST"])
def disable_device(mac: str):
    ok, output = run_barrier_command(["disable", mac])
    return redirect(url_for("index", message=output or "Готово", success="1" if ok else "0"))


@app.route("/remove/<mac>", methods=["POST"])
def remove_device(mac: str):
    ok, output = run_barrier_command(["remove", mac])
    return redirect(url_for("index", message=output or "Готово", success="1" if ok else "0"))


@app.route("/test-open", methods=["POST"])
def test_open():
    ok, output = run_barrier_command(["test-open"])
    return redirect(url_for("index", message=output or "Готово", success="1" if ok else "0"))


@app.route("/restart-bluetooth", methods=["POST"])
def restart_bluetooth():
    cmd = ["bash", "-lc", "sudo systemctl restart bluetooth"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    ok = result.returncode == 0
    return redirect(url_for("index", message=output.strip() or "Bluetooth перезапущен", success="1" if ok else "0"))


if __name__ == "__main__":
    app.run(host=config.host, port=config.port, debug=False)
