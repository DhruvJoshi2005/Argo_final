#!/bin/sh
# Startup script for the Render-hosted Prometheus.
#
# Two things Render needs that a plain Prometheus image doesn't do by default:
#   1. The Grafana Cloud token arrives as an environment variable (a Render
#      secret), but Prometheus can't read env vars inside its YAML config — so
#      we write it to a file that the config's `password_file` points at.
#   2. Render assigns the port dynamically via $PORT, so Prometheus must bind
#      to that instead of its default 9090.

set -e

TOKEN_FILE=/tmp/gc_token

if [ -n "$GRAFANA_CLOUD_TOKEN" ]; then
  # printf (not echo) so no trailing newline sneaks into the token
  printf '%s' "$GRAFANA_CLOUD_TOKEN" > "$TOKEN_FILE"
  echo "startup: Grafana Cloud token loaded, remote_write enabled"
else
  # Still start up — scraping works, only the push to Grafana Cloud will fail.
  printf '' > "$TOKEN_FILE"
  echo "startup: WARNING - GRAFANA_CLOUD_TOKEN is not set; remote_write will fail" >&2
fi

exec /bin/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=6h \
  --web.listen-address="0.0.0.0:${PORT:-9090}" \
  --web.enable-lifecycle
