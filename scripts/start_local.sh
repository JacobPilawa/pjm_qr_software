#!/bin/zsh
set -euo pipefail

cd "${0:A:h}/.."
if [[ -x ../.venv/bin/python ]]; then
  PJM_QR_DEFAULT_PYTHON=../.venv/bin/python
elif [[ -x .venv/bin/python ]]; then
  PJM_QR_DEFAULT_PYTHON=.venv/bin/python
else
  PJM_QR_DEFAULT_PYTHON=python3.11
fi
PJM_QR_PYTHON="${PJM_QR_PYTHON:-$PJM_QR_DEFAULT_PYTHON}"

"$PJM_QR_PYTHON" -m uvicorn backend.app:app --host 0.0.0.0 --port 8766 &
PJM_QR_API_PID=$!
npm run dev &
PJM_QR_UI_PID=$!

cleanup() {
  kill "$PJM_QR_UI_PID" 2>/dev/null || true
  kill "$PJM_QR_API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for attempt in {1..120}; do
  if curl -fsS http://127.0.0.1:8766/api/status >/dev/null 2>&1 && curl -fsS http://127.0.0.1:5174/ >/dev/null 2>&1; then
    open http://127.0.0.1:5174
    break
  fi
  if ! kill -0 "$PJM_QR_API_PID" 2>/dev/null || ! kill -0 "$PJM_QR_UI_PID" 2>/dev/null; then
    echo "PJM QR software stopped before it became ready."
    exit 1
  fi
  sleep 0.1
done

wait "$PJM_QR_UI_PID"
