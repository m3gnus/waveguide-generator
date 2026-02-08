#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT_DIR="$ROOT_DIR/_references/runtime_smoke"
ARTIFACT_PATH="$ARTIFACT_DIR/latest.json"
LOG_PATH="/tmp/mwg-runtime-smoke-backend.log"

mkdir -p "$ARTIFACT_DIR"

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

python3 "$ROOT_DIR/server/app.py" >"$LOG_PATH" 2>&1 &
BACKEND_PID=$!

for _ in $(seq 1 80); do
  if curl -sSf "http://localhost:8000/health" >/tmp/mwg-health.json 2>/dev/null; then
    break
  fi
  sleep 0.25
done

if ! curl -sSf "http://localhost:8000/health" >/tmp/mwg-health.json 2>/dev/null; then
  echo "runtime-smoke: FAIL backend health check"
  tail -n 40 "$LOG_PATH" || true
  exit 1
fi

echo "runtime-smoke: health ok"

cat > /tmp/mwg-solve-success.json <<'JSON'
{
  "mesh": {
    "vertices": [0,0,0, 100,0,0, 0,100,0],
    "indices": [0,1,2],
    "surfaceTags": [2],
    "format": "msh",
    "boundaryConditions": {
      "throat": {"type":"velocity","surfaceTag":2,"value":1.0},
      "wall": {"type":"neumann","surfaceTag":1,"value":0.0},
      "mouth": {"type":"robin","surfaceTag":1,"impedance":"spherical"}
    },
    "metadata": {"ringCount":3,"fullCircle":true}
  },
  "frequency_range": [100, 100],
  "num_frequencies": 1,
  "sim_type": "2",
  "options": {},
  "use_optimized": false,
  "enable_symmetry": false,
  "verbose": false
}
JSON

SUCCESS_RESP="$(curl -s -X POST "http://localhost:8000/api/solve" -H 'Content-Type: application/json' --data @/tmp/mwg-solve-success.json)"
SUCCESS_JOB_ID="$(printf '%s' "$SUCCESS_RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("job_id",""))')"
if [[ -z "$SUCCESS_JOB_ID" ]]; then
  echo "runtime-smoke: FAIL missing success job_id"
  echo "$SUCCESS_RESP"
  exit 1
fi

echo "runtime-smoke: submitted success job $SUCCESS_JOB_ID"

SUCCESS_STATUS=""
for _ in $(seq 1 120); do
  STATUS_JSON="$(curl -s "http://localhost:8000/api/status/$SUCCESS_JOB_ID")"
  SUCCESS_STATUS="$(printf '%s' "$STATUS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
  if [[ "$SUCCESS_STATUS" == "complete" || "$SUCCESS_STATUS" == "error" || "$SUCCESS_STATUS" == "cancelled" ]]; then
    break
  fi
  sleep 0.5
done

if [[ "$SUCCESS_STATUS" != "complete" ]]; then
  echo "runtime-smoke: FAIL success job ended as $SUCCESS_STATUS"
  tail -n 60 "$LOG_PATH" || true
  exit 1
fi

RESULTS_JSON="$(curl -s "http://localhost:8000/api/results/$SUCCESS_JOB_ID")"

python3 - <<PY
import json
from pathlib import Path
artifact = {
  "health": json.loads(Path('/tmp/mwg-health.json').read_text()),
  "success_job_id": "$SUCCESS_JOB_ID",
  "success_results": json.loads('''$RESULTS_JSON'''),
}
Path("$ARTIFACT_PATH").write_text(json.dumps(artifact, indent=2))
PY

echo "runtime-smoke: success path ok"

cat > /tmp/mwg-solve-cancel.json <<'JSON'
{
  "mesh": {
    "vertices": [0,0,0, 1,0,0, 0,1,0],
    "indices": [0,1,2],
    "surfaceTags": [2],
    "format": "msh",
    "boundaryConditions": {
      "throat": {"type":"velocity","surfaceTag":2,"value":1.0},
      "wall": {"type":"neumann","surfaceTag":1,"value":0.0},
      "mouth": {"type":"robin","surfaceTag":1,"impedance":"spherical"}
    },
    "metadata": {"ringCount":3,"fullCircle":true}
  },
  "frequency_range": [100, 200],
  "num_frequencies": 2,
  "sim_type": "2",
  "options": {},
  "use_optimized": true,
  "enable_symmetry": true,
  "verbose": false
}
JSON

CANCEL_RESP="$(curl -s -X POST "http://localhost:8000/api/solve" -H 'Content-Type: application/json' --data @/tmp/mwg-solve-cancel.json)"
CANCEL_JOB_ID="$(printf '%s' "$CANCEL_RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("job_id",""))')"
if [[ -z "$CANCEL_JOB_ID" ]]; then
  echo "runtime-smoke: FAIL missing cancel job_id"
  echo "$CANCEL_RESP"
  exit 1
fi

STOP_JSON="$(curl -s -X POST "http://localhost:8000/api/stop/$CANCEL_JOB_ID")"
STOP_STATUS="$(printf '%s' "$STOP_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')"
if [[ "$STOP_STATUS" != "cancelled" ]]; then
  echo "runtime-smoke: FAIL stop status = $STOP_STATUS"
  echo "$STOP_JSON"
  exit 1
fi

RESULTS_HTTP="$(curl -s -w ' HTTP:%{http_code}' "http://localhost:8000/api/results/$CANCEL_JOB_ID")"
if [[ "$RESULTS_HTTP" != *"HTTP:400" ]]; then
  echo "runtime-smoke: FAIL cancelled results endpoint did not return 400"
  echo "$RESULTS_HTTP"
  exit 1
fi

echo "runtime-smoke: cancellation path ok"
echo "runtime-smoke: artifact written to $ARTIFACT_PATH"
echo "runtime-smoke: PASS"
