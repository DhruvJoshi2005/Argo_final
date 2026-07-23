#!/usr/bin/env bash
#
# Generates live traffic against ARGO so the Grafana dashboard has something
# to show. Creates a deliberate mix so the latency panel is dramatic:
#
#   - CACHE MISSES: several unique questions, each triggering a real (slow)
#     OpenAI intent call the first time  -> high latency (~2s)
#   - CACHE HITS: each question repeated many times -> served from the intent
#     cache, OpenAI skipped              -> tiny latency (~2ms)
#   - Health/root hits: cheap requests for the request-rate & error-rate panels
#
# Usage:  ./generate_traffic.sh          (one burst)
#         while true; do ./generate_traffic.sh; sleep 5; done   (continuous)

BASE="http://127.0.0.1:8000"

QUESTIONS=(
  "average temperature at the equator"
  "what is salinity around the arabian sea"
  "show me pressure in the bay of bengal"
  "average oxygen in the indian ocean"
  "maximum temperature in the andaman sea"
  "salinity at 500 dbar in the arabian sea"
)

echo "-> root + health traffic (request-rate / error-rate panels)"
for i in $(seq 1 15); do curl -s -o /dev/null "$BASE/";       done
for i in $(seq 1 5);  do curl -s -o /dev/null "$BASE/health"; done   # 503s (DB down)

echo "-> chat traffic: each question once (MISS, slow) then repeated (HITS, fast)"
for q in "${QUESTIONS[@]}"; do
  body="{\"question\":\"$q\"}"
  # First hit = cache miss (real OpenAI call, ~2s)
  curl -s -o /dev/null -X POST "$BASE/chat_optimised" \
       -H "Content-Type: application/json" -d "$body"
  # Repeats = cache hits (served in ~2ms)
  for i in $(seq 1 12); do
    curl -s -o /dev/null -X POST "$BASE/chat_optimised" \
         -H "Content-Type: application/json" -d "$body"
  done
  echo "   done: $q"
done

echo "Traffic burst complete."
