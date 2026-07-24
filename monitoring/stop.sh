#!/usr/bin/env bash
# Stops the services started by ./start.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for name in argo prometheus grafana; do
  pidfile="$SCRIPT_DIR/.$name.pid"
  if [ -f "$pidfile" ]; then
    pid="$(cat "$pidfile")"
    if kill "$pid" 2>/dev/null; then
      echo "stopped $name (pid $pid)"
    else
      echo "$name not running (stale pid $pid)"
    fi
    rm -f "$pidfile"
  else
    echo "$name: no pid file (not started by start.sh?)"
  fi
done
