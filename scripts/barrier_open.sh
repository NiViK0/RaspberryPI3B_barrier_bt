#!/usr/bin/env bash
set -Eeuo pipefail

PYTHON_BIN="${BARRIER_PYTHON:-/opt/barrier/venv/bin/python}"
SERVICE_SCRIPT="${BARRIER_SERVICE_SCRIPT:-/opt/barrier/src/barrier_service.py}"

exec "$PYTHON_BIN" "$SERVICE_SCRIPT" emergency-open
