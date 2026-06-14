import json
import re
import psycopg2
import time
import os
from dotenv import load_dotenv
from openai import OpenAI

# =========================
# OPENAI CLIENT
# =========================

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError(f"OPENAI_API_KEY not found or empty. Current value: {OPENAI_API_KEY!r}")


client = OpenAI(api_key=OPENAI_API_KEY)

# =========================
# STEP 1: INTENT EXTRACTION
# =========================

def extract_intent_llm(question: str) -> dict:
    prompt = f"""
You are an intent extraction engine.

RULES:
- Output ONLY valid JSON
- No explanation text
- No SQL
- Allowed keys ONLY:
  metric, geo, time, depth, aggregation
- Use null if value missing

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

    content = response.choices[0].message.content

    # 🔒 HARD SAFETY CHECK
    if not content or not content.strip():
        raise ValueError("LLM returned empty response")

    content = content.strip()

    # 🔒 STRIP MARKDOWN IF PRESENT
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON from LLM:\n{content}")


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
    # -------------------------
    # 1. NORMALIZE AGGREGATION WORDS
    # -------------------------
    agg_map = {
        "average": "avg",
        "mean": "avg",
        "maximum": "max",
        "max": "max",
        "minimum": "min",
        "min": "min"
    }

    # -------------------------
    # 2. SPLIT AGGREGATION FROM METRIC IF EMBEDDED
    # -------------------------
    if intent.get("metric"):
        metric_text = intent["metric"].lower()

        for word, agg in agg_map.items():
            if word in metric_text:
                intent["aggregation"] = agg
                metric_text = metric_text.replace(word, "").strip()

        intent["metric"] = metric_text if metric_text else intent["metric"]

    # -------------------------
    # 3. NORMALIZE EXPLICIT AGGREGATION FIELD
    # -------------------------
    if intent.get("aggregation"):
        intent["aggregation"] = agg_map.get(
            intent["aggregation"].lower(),
            intent["aggregation"]
        )

    # -------------------------
    # 4. NORMALIZE GEO TERMS
    # -------------------------
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

    # -------------------------
    # 5. STORE RAW QUESTION (FOR GROUPING LOGIC)
    # -------------------------
    intent["_raw_question"] = raw_question.lower()

    return intent


# =========================
# STEP 4: FILTER PLANNING
# =========================

def plan_filters(intent: dict) -> list:
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
        "salinity": "salinity",
        "pressure": "pressure"
    }

    return {
        "column": metric_map[intent["metric"]],
        "filters": filters,
        "aggregation": aggregation,
        "grouping": grouping
    }

# =========================
# STEP 8: SQL GENERATION
# =========================

def generate_sql(plan: dict) -> str:
    col = plan["column"]
    agg = plan["aggregation"]

    select = f"SELECT {agg['type'].upper()}({col})" if agg["apply"] else f"SELECT {col}"
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

# =========================
# STEP 9: SQL VALIDATION
# =========================

def validate_sql(sql: str):
    for kw in {"DROP", "DELETE", "UPDATE", "INSERT", "ALTER"}:
        if kw in sql.upper():
            raise ValueError(f"Forbidden SQL keyword: {kw}")

# =========================
# STEP 10: DB EXECUTION
# =========================

DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

def execute_sql(sql: str):
    start = time.perf_counter()
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()

    sql_ms = (time.perf_counter() - start) * 1000
    return rows, sql_ms

# =========================
# STEP 11: MAIN (WITH TIMINGS)
# =========================

def main(user_question: str):
    total_start = time.perf_counter()

    # intent timing
    t_intent_start = time.perf_counter()
    intent = extract_intent_llm(user_question)
    validate_intent(intent)
    intent = normalize_intent(intent, user_question)
    intent_ms = (time.perf_counter() - t_intent_start) * 1000

    # planning
    plan = build_query_plan(
        intent,
        plan_filters(intent),
        plan_aggregation(intent),
        plan_grouping(intent)
    )

    # SQL generation
    sql = generate_sql(plan)

    # 🔥 PRINT SQL (for visibility)
    print("\n🧾 GENERATED SQL (UNOPTIMISED):\n", sql)

    validate_sql(sql)

    # DB execution
    rows, sql_ms = execute_sql(sql)

    total_ms = (time.perf_counter() - total_start) * 1000
    answer = rows[0][0] if rows else None

    return {
        "answer": str(answer) if answer is not None else "No data found",
        "sql": sql,   # 🔥 RETURN GENERATED SQL
        "timing": {
            "intent_ms": round(intent_ms, 2),
            "sql_ms": round(sql_ms, 2),
            "total_ms": round(total_ms, 2),
            "cache_hit": False
        }
    }
