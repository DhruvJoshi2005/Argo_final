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
# STEP 6b: QUERY MODE DETECTION (regex — no LLM call)
# ======================================================

def detect_query_mode(question: str) -> str:
    q = question.lower()
    if re.search(r'\b(average|avg|mean|max|maximum|min|minimum)\b', q):
        return "aggregate"
    if re.search(r'\b(show|list|points|stations|profiles|where|locations|floats)\b', q):
        return "explore"
    return "descriptive"   # default: "what is temperature around X"

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

_METRIC_DISPLAY = {
    "temperature":  "Temperature",
    "salinity":     "Salinity",
    "pressure":     "Pressure",
    "oxygen":       "Dissolved oxygen",
    "chlorophyll":  "Chlorophyll",
    "backscatter":  "Backscatter",
    "ph":           "pH",
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
        "grouping": grouping,
        "query_mode": intent.get("_query_mode", "descriptive"),
    }

# ======================================================
# STEP 8: SQL GENERATION
# Every branch returns count + bounding box (+ time range where
# relevant) alongside the metric, so an answer is never a bare
# number with no indication of how much data backs it.
# ======================================================

def _build_where(filters: list) -> str:
    if not filters:
        return ""
    where = []
    for c, op, v in filters:
        if op == "BETWEEN":
            where.append(f"{c} BETWEEN {v[0]} AND {v[1]}")
        elif op == "IS NOT NULL":
            where.append(f"{c} IS NOT NULL")
        else:
            where.append(f"{c} {op} {v}")
    return " AND ".join(where)


def generate_sql(plan: dict) -> str:
    col = plan["column"]
    mode = plan["query_mode"]
    where_clause = _build_where(plan["filters"])

    if mode == "explore":
        select = f"SELECT latitude, longitude, juld, pressure, platform_number, {col}"
        sql = [select, "FROM float_measurements_flat"]
        if where_clause:
            sql.append(f"WHERE {where_clause}")
        sql.append("ORDER BY juld DESC")
        sql.append("LIMIT 15")
        return "\n".join(sql)

    # aggregate + descriptive share the same rich summary SELECT
    if plan["grouping"]:
        select = (
            f"SELECT {plan['grouping']} AS bucket, "
            f"AVG({col}) AS avg_val, MIN({col}) AS min_val, MAX({col}) AS max_val, "
            f"COUNT({col}) AS n_obs, "
            "MIN(latitude) AS lat_min, MAX(latitude) AS lat_max, "
            "MIN(longitude) AS lon_min, MAX(longitude) AS lon_max"
        )
        sql = [select, "FROM float_measurements_flat"]
        if where_clause:
            sql.append(f"WHERE {where_clause}")
        sql.append("GROUP BY bucket")
        sql.append("ORDER BY bucket")
        sql.append("LIMIT 50")
        return "\n".join(sql)

    select = (
        f"SELECT AVG({col}) AS avg_val, MIN({col}) AS min_val, MAX({col}) AS max_val, "
        f"COUNT({col}) AS n_obs, "
        "MIN(latitude) AS lat_min, MAX(latitude) AS lat_max, "
        "MIN(longitude) AS lon_min, MAX(longitude) AS lon_max, "
        "MIN(juld) AS time_min, MAX(juld) AS time_max"
    )
    sql = [select, "FROM float_measurements_flat"]
    if where_clause:
        sql.append(f"WHERE {where_clause}")
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
# STEP 10b: ANSWER FORMATTING
# Mode-aware — a bare average is misleading over sparse,
# geographically clustered float data, so every answer carries
# count + spatial/temporal extent instead of a lone number.
# ======================================================

def _fmt(v, decimals=2):
    if v is None:
        return "n/a"
    if isinstance(v, (int, float)):
        return f"{v:.{decimals}f}"
    return str(v)


def _fmt_date(v):
    return v.date() if v is not None else "unknown date"


def format_answer(metric_label: str, rows, mode: str, grouped: bool) -> str:
    if not rows:
        return "No matching observations found for this query."

    if mode == "explore":
        lines = []
        for lat, lon, juld, pressure, platform, val in rows[:15]:
            lines.append(
                f"- Float {platform} at ({_fmt(lat)}, {_fmt(lon)}), "
                f"{_fmt_date(juld)}, "
                f"pressure {_fmt(pressure)}: "
                f"{metric_label}={_fmt(val)}"
            )
        return f"Found {len(rows)} matching profiles:\n" + "\n".join(lines)

    if grouped:
        lines = []
        for bucket, avg_val, min_val, max_val, n_obs, lat_min, lat_max, lon_min, lon_max in rows:
            if not n_obs or avg_val is None:
                lines.append(f"- Bucket {bucket}: no data")
                continue
            lines.append(
                f"- Bucket {bucket}: {metric_label} avg {_fmt(avg_val)} "
                f"(range {_fmt(min_val)}-{_fmt(max_val)}), n={n_obs}, "
                f"lat {_fmt(lat_min, 1)}-{_fmt(lat_max, 1)}, "
                f"lon {_fmt(lon_min, 1)}-{_fmt(lon_max, 1)}"
            )
        return "\n".join(lines)

    # single-row aggregate/descriptive summary
    (avg_val, min_val, max_val, n_obs,
     lat_min, lat_max, lon_min, lon_max, t_min, t_max) = rows[0]

    if not n_obs or avg_val is None:
        return "No matching observations found in this region for the given filters."

    caveat = (
        f" (based on only {n_obs} observations — limited data in this region)"
        if n_obs < 20 else ""
    )

    return (
        f"{metric_label} averages {_fmt(avg_val)} (range {_fmt(min_val)}-{_fmt(max_val)}) "
        f"across {n_obs} observations, spanning lat {_fmt(lat_min, 1)}-{_fmt(lat_max, 1)}, "
        f"lon {_fmt(lon_min, 1)}-{_fmt(lon_max, 1)}, "
        f"from {_fmt_date(t_min)} to {_fmt_date(t_max)}."
        f"{caveat}"
    )

# ======================================================
# STEP 11: MAIN ENTRY
# ======================================================

def main(user_question: str):
    total_start = time.perf_counter()

    intent, intent_ms, intent_cache_hit = extract_intent_llm(user_question)
    validate_intent(intent)
    intent = normalize_intent(intent, user_question)
    intent["_query_mode"] = detect_query_mode(user_question)
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
    metric_label = _METRIC_DISPLAY.get(intent["metric"], intent["metric"].title())
    answer = format_answer(
        metric_label, rows, plan["query_mode"], grouped=bool(plan["grouping"])
    )

    return {
        "answer": answer,
        "sql": sql,   # 🔥 RETURNING GENERATED SQL
        "timing": {
            "intent_ms": round(intent_ms, 2),
            "sql_ms": round(sql_ms, 2),
            "total_ms": round(total_ms, 2),
            "cache_hit": intent_cache_hit or sql_cache_hit
        }
    }
