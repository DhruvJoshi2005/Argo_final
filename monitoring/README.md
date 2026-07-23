# ARGO Monitoring — Prometheus + Grafana

Live monitoring for the ARGO FastAPI backend: request rate, latency percentiles
(p50/p95/p99), and error rate per endpoint, on a Grafana dashboard.

**Why it exists:** ARGO uses an intent cache + SQL-result cache + DB connection
pool to bring repeated-query latency from ~2.5 s down to a few milliseconds. This
dashboard makes that optimization *visible* — the latency panel shows cache hits
landing at ~2 ms while uncached requests (a real OpenAI call) sit up near ~2.5 s.

```
ARGO app (FastAPI)  --/metrics-->  Prometheus  --queries-->  Grafana dashboard
   port 8000                        port 9090                    port 3000
```

- **Prometheus** = the collector. Every 5 s it reads ARGO's `/metrics` page and
  stores the numbers over time.
- **Grafana** = the visualizer. It queries Prometheus and draws the graphs.
- Neither uses Docker; both run as plain downloaded binaries.

---

## Prerequisites (one-time setup)

The Python virtual environment and the Prometheus/Grafana binaries are **not**
committed to git (too large). Recreate them once:

```bash
# 1. Python env for running ARGO + the metrics library
cd <project-root>
python3 -m venv .venv
./.venv/bin/pip install fastapi uvicorn psycopg2-binary openai python-dotenv \
    prometheus-fastapi-instrumentator
# (or ./.venv/bin/pip install -r requirements.txt for the full project)

# 2. Prometheus binary  -> monitoring/prometheus/
cd monitoring
curl -sL -o prom.tgz https://github.com/prometheus/prometheus/releases/download/v3.13.1/prometheus-3.13.1.linux-amd64.tar.gz
tar xzf prom.tgz && mv prometheus-3.13.1.linux-amd64 prometheus && rm prom.tgz

# 3. Grafana binary  -> monitoring/grafana/
curl -sL -o graf.tgz https://dl.grafana.com/oss/release/grafana-13.1.1.linux-amd64.tar.gz
tar xzf graf.tgz && mv grafana-v13.1.1 grafana && rm graf.tgz
```

(Versions above are what this was built with; newer point releases work too.)

---

## Running it

**All at once:**
```bash
cd monitoring
./start.sh            # starts ARGO + Prometheus + Grafana in the background
./generate_traffic.sh # optional: create demo traffic so the graphs have data
./stop.sh             # stops all three
```

Then open **http://localhost:3000** → log in `admin` / `admin` → **Dashboards →
ARGO — API Monitoring**. Set refresh to 5 s and range to "Last 15 minutes".

**Manual (what start.sh does, if you want to run them one at a time):**
```bash
# ARGO
./.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000

# Prometheus (from monitoring/)
./prometheus/prometheus --config.file=prometheus.yml \
    --storage.tsdb.path=./prometheus/data --web.listen-address=127.0.0.1:9090

# Grafana (from monitoring/)
GF_PATHS_PROVISIONING="$PWD/grafana-provisioning" \
GF_PATHS_DATA="$PWD/grafana-data" GF_PATHS_LOGS="$PWD/grafana-logs" \
GF_SERVER_HTTP_PORT=3000 ./grafana/bin/grafana server --homepath "$PWD/grafana"
```

| URL | What's there |
|---|---|
| http://localhost:8000/metrics | Raw metrics ARGO exposes |
| http://localhost:9090 | Prometheus — **Status → Targets** (should be UP), **Alerts** |
| http://localhost:3000 | Grafana dashboard |

---

## Dashboard panels & the PromQL behind them

| Panel | PromQL | What it means |
|---|---|---|
| **Total Requests** | `sum(http_requests_total{job="argo"})` | Cumulative count of every request handled. |
| **Request Rate per Endpoint** | `sum(rate(http_requests_total{job="argo"}[1m])) by (handler)` | Requests/second for each route, averaged over the last minute. |
| **Latency p50** | `histogram_quantile(0.50, sum(rate(http_request_duration_highr_seconds_bucket{job="argo"}[1m])) by (le))` | Median response time — dominated by fast cache hits. |
| **Latency p95** | same with `0.95` | 95th-percentile — starts catching the slow uncached calls. |
| **Latency p99** | same with `0.99` | 99th-percentile — the worst-case tail (real OpenAI calls, ~2.5 s). |
| **Error Rate** | `sum(rate(http_requests_total{job="argo", status=~"4xx\|5xx"}[1m])) by (handler, status)` | Failed responses/second per endpoint + status class. |

**Reading the terms:**
- `rate(...[1m])` = per-second average change over the last minute (turns an
  ever-increasing counter into a "how fast is this happening right now" rate).
- `histogram_quantile(0.95, ...)` = "95% of requests were faster than this value."
  It's computed from latency *buckets* — ARGO is configured with fine buckets
  down to 1 ms (see `main.py`) specifically so sub-5 ms cache hits are visible.
- `job="argo"` = only ARGO's metrics (Prometheus also scrapes itself).

---

## Alert rules (`alert.rules.yml`)

Loaded into Prometheus; visible at http://localhost:9090/alerts.

- **HighP95Latency** — fires if p95 latency stays above 1 s for 1 minute. With
  caching working, p95 sits at a few ms, so this only trips if the optimization
  regresses or a flood of uncached requests arrives.
- **ArgoDown** — fires if Prometheus can't scrape ARGO for 30 s (app crashed/stopped).

In production these would notify Slack/email/PagerDuty via a contact point; here
they demonstrate the alerting concept.

---

## Exporting / re-importing the dashboard

The dashboard already lives in git as
`grafana-provisioning/dashboards/argo-dashboard.json`, and is auto-loaded on every
Grafana start — so it survives restarts and reinstalls with no manual import.

If you edit it in the Grafana UI and want to save those changes back to the repo:
**Dashboard → Share/Export → Export → Save to file** (or **Dashboard settings →
JSON Model → copy**), then paste over `argo-dashboard.json`.

To import a dashboard JSON into a fresh Grafana manually:
**Dashboards → New → Import → Upload JSON file**.

---

## Screenshots (backup for the interview)

In case you can't run the stack live during the interview, keep static images:
1. Run `./start.sh` then `./generate_traffic.sh` (run the traffic script a few
   times so the latency spread is visible).
2. Open the dashboard, set range to "Last 15 minutes".
3. Screenshot the whole dashboard (especially the **Latency p50/p95/p99** panel
   showing p50 ≈ few ms vs p99 ≈ seconds).
4. Also screenshot Prometheus **Status → Targets** (argo UP) and **Alerts**.
5. Save them in `monitoring/screenshots/` (create the folder; small PNGs are fine
   to commit).

---

## 30-second interview summary

> "I instrumented the ARGO FastAPI backend with Prometheus using the
> `prometheus-fastapi-instrumentator` library, which exposes request-count,
> latency-histogram, and status metrics on a `/metrics` endpoint. Prometheus
> scrapes that every 5 seconds, and I built a Grafana dashboard on top showing
> request rate, p50/p95/p99 latency, and error rate per endpoint. The payoff
> panel is latency: you can watch cache hits land at around 2 milliseconds while
> uncached queries — which make a real LLM call — sit up near 2.5 seconds, so my
> caching optimization is visible in real time. I also added a Prometheus alert
> on p95 latency so a regression would page me. It all runs as standalone
> binaries, started with one script, with the dashboard defined as JSON in the repo."
