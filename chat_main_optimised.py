import json
import re
import time
import os
from dotenv import load_dotenv
from psycopg2 import pool
from openai import OpenAI

# ======================================================
# STEP 0: GLOBALS
# ======================================================

# 🔥 Intent cache (question → intent)
INTENT_CACHE = {}

# 🔥 SQL result cache (sql → rows)
SQL_CACHE = {}

load_dotenv()
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

# 🔥 DB CONNECTION POOL
DB_POOL = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    **DB_PARAMS
)

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError(f"OPENAI_API_KEY not found or empty. Current value: {OPENAI_API_KEY!r}")


client = OpenAI(api_key=OPENAI_API_KEY)

# ======================================================
# STEP 1: INTENT EXTRACTION (LLM + CACHE)
# ======================================================

def extract_intent_llm(question: str):
    start = time.perf_counter()
    cache_key = question.strip().lower()

    # ---------- INTENT CACHE ----------
    if cache_key in INTENT_CACHE:
        intent = dict(INTENT_CACHE[cache_key])
        intent_ms = (time.perf_counter() - start) * 1000
        return intent, intent_ms, True

    prompt = f"""
You are a STRICT intent extraction engine.

RULES:
- Output ONLY valid JSON
- Allowed keys ONLY:
  metric, geo, time, depth, aggregation
- Use null if missing

If question is vague:
- metric = temperature
- aggregation = avg

USER QUESTION:
{question}
"""

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

    intent = json.loads(content)

    if intent.get("aggregation") is None:
        intent["aggregation"] = "avg"

    INTENT_CACHE[cache_key] = dict(intent)

    intent_ms = (time.perf_counter() - start) * 1000
    return intent, intent_ms, False


# ======================================================
# STEP 2: INTENT VALIDATION
# ======================================================

def validate_intent(intent: dict):
    allowed = {"metric", "geo", "time", "depth", "aggregation"}
    for k in intent:
        if k not in allowed:
            raise ValueError(f"Invalid intent key: {k}")
    if not intent.get("metric"):
        raise ValueError("Metric is required")


# ======================================================
# STEP 3: INTENT NORMALIZATION
# ======================================================

def normalize_intent(intent: dict, raw_question: str):
    geo_map = {
        "equator": "geo_equator",
        "equatorial region": "geo_equator",
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

    intent["_raw_question"] = raw_question.lower()
    return intent

# ======================================================
# STEP 4: FILTER PLANNING
# ======================================================

def plan_filters(intent: dict):
    filters = []

    geo_boxes = {
        "geo_equator": (-10, 10, -180, 180),
        "geo_tropic_cancer": (15, 30, -180, 180),
        "geo_tropic_capricorn": (-30, -15, -180, 180),
        "geo_pacific": (-60, 60, 120, -70),
        "geo_atlantic": (-60, 60, -70, 20),
        "geo_indian": (-60, 30, 20, 120),
        "geo_southern": (-90, -55, -180, 180),
        "geo_arctic": (55, 90, -180, 180)
    }

    geo = intent.get("geo")
    if geo in geo_boxes:
        lat_min, lat_max, lon_min, lon_max = geo_boxes[geo]
        filters.append(("latitude", "BETWEEN", (lat_min, lat_max)))
        filters.append(("longitude", "BETWEEN", (lon_min, lon_max)))

    if intent.get("time") is not None:
        filters.append(("juld", "=", intent["time"]))

    if intent.get("depth") is not None:
        filters.append(("pressure", "=", intent["depth"]))

    return filters

# ======================================================
# STEP 5: AGGREGATION
# ======================================================

def plan_aggregation(intent: dict):
    agg = intent.get("aggregation")
    if agg in {"avg", "min", "max"}:
        return {"apply": True, "type": agg}
    return {"apply": False, "type": None}

# ======================================================
# STEP 6: GROUPING
# ======================================================

def plan_grouping(intent: dict):
    q = intent.get("_raw_question", "")
    m = re.search(r"per\s+(\d+)\s+cycles", q)
    if not m:
        return None
    return f"(cycle_number / {int(m.group(1))})"

# ======================================================
# STEP 7: QUERY PLAN
# ======================================================

def build_query_plan(intent, filters, aggregation, grouping):
    metric_map = {
        "temperature": "temperature",
        "salinity": "salinity",
        "pressure": "pressure"
    }

    metric = intent["metric"]
    if metric not in metric_map:
        raise ValueError(f"Unsupported metric: {metric}")

    return {
        "column": metric_map[metric],
        "filters": filters,
        "aggregation": aggregation,
        "grouping": grouping
    }

# ======================================================
# STEP 8: SQL GENERATION
# ======================================================

def generate_sql(plan: dict):
    col = plan["column"]
    agg = plan["aggregation"]

    if agg["apply"]:
        select = f"SELECT {agg['type'].upper()}({col})"
    else:
        select = f"SELECT {col}"

    sql = [select, "FROM float_measurements_flat"]

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

    return "\n".join(sql)

# ======================================================
# STEP 9: SQL VALIDATION
# ======================================================

def validate_sql(sql: str):
    forbidden = {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER"}
    for kw in forbidden:
        if kw in sql.upper():
            raise ValueError(f"Forbidden SQL keyword: {kw}")

# ======================================================
# STEP 10: DB EXECUTION (POOLING)
# ======================================================

def execute_sql(sql: str):
    # 🔥 SQL RESULT CACHE
    if sql in SQL_CACHE:
        return SQL_CACHE[sql], 0.0, True

    start = time.perf_counter()
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        DB_POOL.putconn(conn)

    sql_ms = (time.perf_counter() - start) * 1000
    SQL_CACHE[sql] = rows
    return rows, sql_ms, False

# ======================================================
# STEP 11: MAIN ENTRY
# ======================================================

def main(user_question: str):
    total_start = time.perf_counter()

    intent, intent_ms, intent_cache_hit = extract_intent_llm(user_question)
    validate_intent(intent)
    intent = normalize_intent(intent, user_question)

    plan = build_query_plan(
        intent,
        plan_filters(intent),
        plan_aggregation(intent),
        plan_grouping(intent)
    )

    sql = generate_sql(plan)
    print("\n🧾 GENERATED SQL:\n", sql)

    validate_sql(sql)

    rows, sql_ms, sql_cache_hit = execute_sql(sql)

    total_ms = (time.perf_counter() - total_start) * 1000
    answer = rows[0][0] if rows else None

    return {
        "answer": str(answer) if answer is not None else "No data found",
        "sql": sql,   # 🔥 RETURNING GENERATED SQL
        "timing": {
            "intent_ms": round(intent_ms, 2),
            "sql_ms": round(sql_ms, 2),
            "total_ms": round(total_ms, 2),
            "cache_hit": intent_cache_hit or sql_cache_hit
        }
    }
