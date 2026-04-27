#!/usr/bin/env bash
set -Eeuo pipefail

epoch="${1:-}"

if [[ ! "$epoch" =~ ^[0-9]{10}$ ]]; then
  echo "Usage: barrier-set-time <unix-epoch-seconds>" >&2
  exit 2
fi

min_epoch=1704067200 # 2024-01-01T00:00:00Z
max_epoch=1893456000 # 2030-01-01T00:00:00Z

if (( epoch < min_epoch || epoch > max_epoch )); then
  echo "Refusing suspicious time value: ${epoch}" >&2
  exit 2
fi

date -u -s "@${epoch}"
hwclock -w 2>/dev/null || true
