#!/bin/sh
# Prometheus can't read env vars in its YAML, so write the Grafana Cloud token
# to a file for password_file, and bind to Render's dynamic $PORT.

set -e

TOKEN_FILE=/tmp/gc_token

if [ -n "$GRAFANA_CLOUD_TOKEN" ]; then
  printf '%s' "$GRAFANA_CLOUD_TOKEN" > "$TOKEN_FILE"
  echo "startup: Grafana Cloud token loaded, remote_write enabled"
else
  # Still start: scraping works, only remote_write will fail.
  printf '' > "$TOKEN_FILE"
  echo "startup: WARNING - GRAFANA_CLOUD_TOKEN is not set; remote_write will fail" >&2
fi

exec /bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=6h \
  --web.listen-address="0.0.0.0:${PORT:-9090}" \
  --web.enable-lifecycle
