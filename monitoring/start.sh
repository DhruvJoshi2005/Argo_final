#!/usr/bin/env bash
# Starts ARGO API + Prometheus + Grafana in the background (logs: monitoring/*.log).
# Stop with ./stop.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Point the dashboard provider at wherever this repo currently lives.
python3 - "$SCRIPT_DIR" <<'PY'
import sys, re, pathlib
sd = sys.argv[1]
f = pathlib.Path(sd) / "grafana-provisioning" / "dashboards" / "dashboard-provider.yml"
t = f.read_text()
t = re.sub(r'(\n\s*path:\s*).*', rf'\1"{sd}/grafana-provisioning/dashboards"', t, count=1)
f.write_text(t)
PY

echo "Starting ARGO API (port 8000)..."
cd "$PROJECT_DIR"
nohup ./.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 \
  > "$SCRIPT_DIR/argo.log" 2>&1 &
echo $! > "$SCRIPT_DIR/.argo.pid"

echo "Starting Prometheus (port 9090)..."
cd "$SCRIPT_DIR"
nohup ./prometheus/prometheus \
  --config.file=prometheus.yml \
  --storage.tsdb.path=./prometheus/data \
  --web.listen-address=127.0.0.1:9090 \
  --web.enable-lifecycle \
  > "$SCRIPT_DIR/prometheus.log" 2>&1 &
echo $! > "$SCRIPT_DIR/.prometheus.pid"

echo "Starting Grafana (port 3000)..."
export GF_PATHS_PROVISIONING="$SCRIPT_DIR/grafana-provisioning"
export GF_PATHS_DATA="$SCRIPT_DIR/grafana-data"
export GF_PATHS_LOGS="$SCRIPT_DIR/grafana-logs"
export GF_SERVER_HTTP_ADDR="127.0.0.1"
export GF_SERVER_HTTP_PORT="3000"
export GF_ANALYTICS_REPORTING_ENABLED="false"
export GF_ANALYTICS_CHECK_FOR_UPDATES="false"
nohup ./grafana/bin/grafana server --homepath "$SCRIPT_DIR/grafana" \
  > "$SCRIPT_DIR/grafana.log" 2>&1 &
echo $! > "$SCRIPT_DIR/.grafana.pid"

echo ""
echo "All started."
echo "  ARGO       -> http://localhost:8000  (/metrics for raw metrics)"
echo "  Prometheus -> http://localhost:9090  (Status -> Targets, Alerts)"
echo "  Grafana    -> http://localhost:3000  (admin/admin -> Dashboards -> ARGO)"
echo ""
echo "Generate demo traffic with:  ./generate_traffic.sh"
echo "Stop everything with:        ./stop.sh"
