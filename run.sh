#!/bin/sh
cd "$(dirname "$0")"; set -a; [ -f .env ] && . ./.env; set +a
exec ${PY:-.venv/bin/python} -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8765} --proxy-headers --forwarded-allow-ips='*'
