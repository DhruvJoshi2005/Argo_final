#!/usr/bin/env bash
# Generates demo traffic: unique questions (cache misses, ~2s) and repeats
# (cache hits, ~2ms), plus cheap root/health hits.
#
# Usage: ./generate_traffic.sh

BASE="http://127.0.0.1:8000"

QUESTIONS=(
  "average temperature at the equator"
  "what is salinity around the arabian sea"
  "show me pressure in the bay of bengal"
  "average oxygen in the indian ocean"
  "maximum temperature in the andaman sea"
  "salinity at 500 dbar in the arabian sea"
)

echo "-> root + health traffic"
for i in $(seq 1 15); do curl -s -o /dev/null "$BASE/";       done
for i in $(seq 1 5);  do curl -s -o /dev/null "$BASE/health"; done   # 503s (DB down)

echo "-> chat traffic: each question once, then repeated"
for q in "${QUESTIONS[@]}"; do
  body="{\"question\":\"$q\"}"
  curl -s -o /dev/null -X POST "$BASE/chat_optimised" \
       -H "Content-Type: application/json" -d "$body"
  for i in $(seq 1 12); do
    curl -s -o /dev/null -X POST "$BASE/chat_optimised" \
         -H "Content-Type: application/json" -d "$body"
  done
  echo "   done: $q"
done

echo "Traffic burst complete."
