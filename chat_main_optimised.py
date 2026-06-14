import json
import re
import time
import os
from collections import OrderedDict
from dotenv import load_dotenv
from psycopg2 import pool
from openai import OpenAI

# ======================================================
# STEP 0: GLOBALS
# ======================================================

# LRU cache — bounded size, evicts oldest on overflow
class LRUCache:
    def __init__(self, max_size: int):
        self._cache = OrderedDict()
        self._max_size = max_size

    def get(self, key):
        if key not in self._cache:
            return None, False
        self._cache.move_to_end(key)
        return self._cache[key], True

    def set(self, key, value):
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def clear(self):
        self._cache.clear()

    def __len__(self):
        return len(self._cache)


# Intent cache: max 500 unique questions
INTENT_CACHE = LRUCache(max_size=500)

# SQL result cache: max 200 unique queries
SQL_CACHE = LRUCache(max_size=200)

load_dotenv()
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

# ThreadedConnectionPool — thread-safe for FastAPI/uvicorn
DB_POOL = pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=10,
    **DB_PARAMS
)


def clear_sql_cache():
    """Call after /refresh_data so stale SQL results are evicted."""
    SQL_CACHE.clear()


def _cache_key(question: str) -> str:
    """Normalize question for cache key — strips punctuation and extra spaces."""
    q = question.lower().strip()
    q = re.sub(r'[^\w\s]', '', q)
    q = re.sub(r'\s+', ' ', q)
    return q

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
    cache_key = _cache_key(question)

    # ---------- INTENT CACHE ----------
    cached, hit = INTENT_CACHE.get(cache_key)
    if hit:
        intent_ms = (time.perf_counter() - start) * 1000
        return dict(cached), intent_ms, True

    prompt = f"""
You are a STRICT intent extraction engine.

RULES:
- Output ONLY valid JSON
- Allowed keys ONLY:
  metric, geo, time, depth, aggregation
- Use null if missing

Supported metrics: temperature, salinity, pressure, oxygen, chlorophyll, backscatter, ph

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

    INTENT_CACHE.set(cache_key, dict(intent))

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

    # Normalize metric synonyms
    metric_synonyms = {
        "dissolved oxygen": "oxygen",
        "doxy": "oxygen",
        "o2": "oxygen",
        "chlorophyll-a": "chlorophyll",
        "chla": "chlorophyll",
        "bbp700": "backscatter",
        "ph_in_situ_total": "ph",
        "acidity": "ph",
        "alkalinity": "ph",
    }
    if intent.get("metric"):
        m = intent["metric"].lower().strip()
        intent["metric"] = metric_synonyms.get(m, m)

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

_METRIC_MAP = {
    "temperature":  "temperature",
    "salinity":     "salinity",
    "pressure":     "pressure",
    "oxygen":       "doxy",
    "chlorophyll":  "chla",
    "backscatter":  "bbp700",
    "ph":           "ph_in_situ_total",
}

_BIO_COLUMNS = {"doxy", "chla", "bbp700", "ph_in_situ_total"}


def validate_metric(intent: dict):
    metric = intent.get("metric", "")
    if metric not in _METRIC_MAP:
        raise ValueError(
            f"Unsupported metric: '{metric}'. "
            f"Supported: {', '.join(sorted(_METRIC_MAP))}"
        )


def build_query_plan(intent, filters, aggregation, grouping):
    metric = intent["metric"]
    column = _METRIC_MAP[metric]

    # Bio columns: filter out NULL rows so aggregation is meaningful
    filters = list(filters)
    if column in _BIO_COLUMNS:
        filters.append((column, "IS NOT NULL", None))

    return {
        "column": column,
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
            elif op == "IS NOT NULL":
                where.append(f"{c} IS NOT NULL")
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
    cached, hit = SQL_CACHE.get(sql)
    if hit:
        return cached, 0.0, True

    start = time.perf_counter()
    conn = DB_POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        DB_POOL.putconn(conn)

    sql_ms = (time.perf_counter() - start) * 1000
    SQL_CACHE.set(sql, rows)
    return rows, sql_ms, False

# ======================================================
# STEP 11: MAIN ENTRY
# ======================================================

def main(user_question: str):
    total_start = time.perf_counter()

    intent, intent_ms, intent_cache_hit = extract_intent_llm(user_question)
    validate_intent(intent)
    intent = normalize_intent(intent, user_question)
    validate_metric(intent)  # fail fast before any planning

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
