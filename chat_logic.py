import json
import re
import psycopg2
import time
import os
from dotenv import load_dotenv
from openai import OpenAI

from rate_limit import consume_llm_budget


# =============================================================
# BAD PRACTICE: No global client, no pool, no cache.
# Every helper re-reads .env and rebuilds objects from scratch.
# =============================================================

def _fresh_openai_client():
    load_dotenv()                            # BAD: disk read on every call
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))   # BAD: new object every call


def _fresh_db_conn():
    load_dotenv()                            # BAD: disk read again
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "argo_final"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD")   # BAD: new TCP handshake every call
    )


# =========================
# STEP 1: INTENT EXTRACTION
# No cache — LLM called on every single request
# =========================

def extract_intent_llm(question: str) -> dict:
    client = _fresh_openai_client()          # BAD: brand new client every request

    prompt = f"""
You are an intent extraction engine.

RULES:
- Output ONLY valid JSON
- No explanation text
- No SQL
- Allowed keys ONLY:
  metric, geo, time, depth, aggregation
- Use null if value missing

Supported metrics: temperature, salinity, pressure, oxygen, chlorophyll, backscatter, ph

USER QUESTION:
{question}
"""

    # This path has no cache, so every call is a real (paid) OpenAI call.
    # Enforce the daily budget before spending money.
    consume_llm_budget()

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": "Return ONLY JSON."},
            {"role": "user", "content": prompt}
        ]
    )

    content = response.choices[0].message.content.strip()

    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()

    return json.loads(content)


# =========================
# STEP 2: INTENT VALIDATION
# =========================

def validate_intent(intent: dict):
    allowed_keys = {"metric", "geo", "time", "depth", "aggregation"}
    for key in intent:
        if key not in allowed_keys:
            raise ValueError(f"Invalid intent key: {key}")
    if not intent.get("metric"):
        raise ValueError("Metric is required")


# =========================
# STEP 3: INTENT NORMALIZATION
# =========================

def normalize_intent(intent: dict, raw_question: str) -> dict:
    agg_map = {
        "average": "avg", "mean": "avg",
        "maximum": "max", "max": "max",
        "minimum": "min", "min": "min"
    }

    if intent.get("metric"):
        metric_text = intent["metric"].lower()
        for word, agg in agg_map.items():
            if word in metric_text:
                intent["aggregation"] = agg
                metric_text = metric_text.replace(word, "").strip()
        intent["metric"] = metric_text if metric_text else intent["metric"]

    if intent.get("aggregation"):
        intent["aggregation"] = agg_map.get(
            intent["aggregation"].lower(), intent["aggregation"]
        )

    geo_map = {
        "equator": "geo_equator",
        "tropic of cancer": "geo_tropic_cancer",
        "tropic of capricorn": "geo_tropic_capricorn",
        "pacific ocean": "geo_pacific",
        "atlantic ocean": "geo_atlantic",
        "indian ocean": "geo_indian",
        "southern ocean": "geo_southern",
        "arctic ocean": "geo_arctic"
    }
    if intent.get("geo"):
        g = intent["geo"].lower()
        if g in geo_map:
            intent["geo"] = geo_map[g]

    metric_synonyms = {
        "dissolved oxygen": "oxygen", "doxy": "oxygen", "o2": "oxygen",
        "chlorophyll-a": "chlorophyll", "chla": "chlorophyll",
        "bbp700": "backscatter",
        "ph_in_situ_total": "ph", "acidity": "ph", "alkalinity": "ph",
    }
    if intent.get("metric"):
        m = intent["metric"].lower().strip()
        intent["metric"] = metric_synonyms.get(m, m)

    intent["_raw_question"] = raw_question.lower()
    return intent


# =========================
# STEP 4: FILTER PLANNING
# =========================

def plan_filters(intent: dict) -> list:
    filters = []
    geo_boxes = {
        "geo_equator":          (-10, 10,   -180, 180),
        "geo_tropic_cancer":    (15,  30,   -180, 180),
        "geo_tropic_capricorn": (-30, -15,  -180, 180),
        "geo_pacific":          (-60, 60,    120, -70),
        "geo_atlantic":         (-60, 60,   -70,   20),
        "geo_indian":           (-60, 30,    20,  120),
        "geo_southern":         (-90, -55, -180,  180),
        "geo_arctic":           (55,  90,  -180,  180)
    }

    geo = intent.get("geo")
    if geo in geo_boxes:
        lat_min, lat_max, lon_min, lon_max = geo_boxes[geo]
        filters.append(("latitude",  "BETWEEN", (lat_min, lat_max)))
        filters.append(("longitude", "BETWEEN", (lon_min, lon_max)))

    if intent.get("time") is not None:
        filters.append(("juld", "=", intent["time"]))

    if intent.get("depth") is not None:
        filters.append(("pressure", "=", intent["depth"]))

    return filters


# =========================
# STEP 5: AGGREGATION
# =========================

def plan_aggregation(intent: dict) -> dict:
    agg = intent.get("aggregation")
    if agg in {"avg", "min", "max"}:
        return {"apply": True, "type": agg}
    return {"apply": False, "type": None}


# =========================
# STEP 6: GROUPING
# =========================

def plan_grouping(intent: dict):
    q = intent.get("_raw_question", "")
    m = re.search(r"per\s+(\d+)\s+cycles", q)
    if not m:
        return None
    return f"(cycle_number / {int(m.group(1))})"


# =========================
# STEP 7: QUERY PLAN
# =========================

def build_query_plan(intent, filters, aggregation, grouping):
    metric_map = {
        "temperature": "temperature",
        "salinity":    "salinity",
        "pressure":    "pressure",
        "oxygen":      "doxy",
        "chlorophyll": "chla",
        "backscatter": "bbp700",
        "ph":          "ph_in_situ_total",
    }
    return {
        "column":      metric_map[intent["metric"]],
        "filters":     filters,
        "aggregation": aggregation,
        "grouping":    grouping
    }


# =========================
# STEP 8: SQL GENERATION
# BAD: No DB-side aggregation — SELECT raw column only.
# All rows come to Python; Python does the math.
# =========================

def generate_sql(plan: dict):
    col = plan["column"]
    agg = plan["aggregation"]

    # BAD: Never push AVG/MIN/MAX into the database.
    # Always pull every individual row and aggregate in Python.
    sql = [f"SELECT {col}", "FROM float_measurements_flat"]

    if plan["filters"]:
        where = []
        for c, op, v in plan["filters"]:
            if op == "BETWEEN":
                where.append(f"{c} BETWEEN {v[0]} AND {v[1]}")
            else:
                where.append(f"{c} {op} {v}")
        sql.append("WHERE " + " AND ".join(where))

    if plan["grouping"]:
        sql.append(f"GROUP BY {plan['grouping']}")

    agg_type = agg["type"] if agg["apply"] else None
    return "\n".join(sql), agg_type


# =========================
# STEP 9: SQL VALIDATION
# =========================

def validate_sql(sql: str):
    for kw in {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER"}:
        if kw in sql.upper():
            raise ValueError(f"Forbidden SQL keyword: {kw}")


# =========================
# STEP 10: DB EXECUTION
# BAD: Two separate connections per request.
# BAD: Fetches every row into Python memory, then aggregates in a loop.
# =========================

def execute_sql(sql: str, agg_type: str):
    start = time.perf_counter()

    # BAD: Open connection #1 just to check the table exists (pointless round-trip)
    check_conn = _fresh_db_conn()
    try:
        with check_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'float_measurements_flat'"
            )
            cur.fetchone()
    finally:
        check_conn.close()   # close it immediately after one query

    # BAD: Open connection #2 for the actual query (new TCP handshake again)
    conn = _fresh_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()   # BAD: pulls ALL matching rows into Python RAM
    finally:
        conn.close()

    # BAD: Aggregate in Python instead of letting the database do it
    values = [r[0] for r in rows if r[0] is not None]

    if not values:
        result = None
    elif agg_type == "avg":
        result = sum(values) / len(values)   # BAD: Python loop over all rows
    elif agg_type == "max":
        result = max(values)
    elif agg_type == "min":
        result = min(values)
    else:
        result = values[0] if values else None

    sql_ms = (time.perf_counter() - start) * 1000
    return result, sql_ms


# =========================
# STEP 11: MAIN (WITH TIMINGS)
# =========================

def main(user_question: str):
    total_start = time.perf_counter()

    t_intent_start = time.perf_counter()
    intent = extract_intent_llm(user_question)    # no cache, always calls LLM
    validate_intent(intent)
    intent = normalize_intent(intent, user_question)
    intent_ms = (time.perf_counter() - t_intent_start) * 1000

    plan = build_query_plan(
        intent,
        plan_filters(intent),
        plan_aggregation(intent),
        plan_grouping(intent)
    )

    sql, agg_type = generate_sql(plan)
    print("\n🐌 GENERATED SQL (UNOPTIMISED):\n", sql)

    validate_sql(sql)

    result, sql_ms = execute_sql(sql, agg_type)

    total_ms = (time.perf_counter() - total_start) * 1000

    return {
        "answer": str(result) if result is not None else "No data found",
        "sql": sql,
        "timing": {
            "intent_ms": round(intent_ms, 2),
            "sql_ms":    round(sql_ms, 2),
            "total_ms":  round(total_ms, 2),
            "cache_hit": False
        }
    }
