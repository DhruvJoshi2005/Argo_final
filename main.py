import io
import csv
import logging
import os

import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator, metrics

from rate_limit import rate_guard, budget_status

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("argo_api.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

from chat_logic import main as chat_main
from chat_main_optimised import main as chat_main_optimised, clear_sql_cache

DB_PARAMS = {
    "host":     os.getenv("DB_HOST", "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
    "sslmode":  os.getenv("DB_SSLMODE", "disable"),
}


def _db():
    return psycopg2.connect(**DB_PARAMS)


# ===============================
# APP INIT
# ===============================
app = FastAPI(
    title="ARGO Data Backend",
    description="Natural-language API over INCOIS Argo float oceanographic data.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# METRICS — Prometheus (/metrics)
# Records request count, latency, and status per endpoint.
# Purely additive: does not alter any existing route or logic.
#
# Custom fine-grained latency buckets (down to 1ms) so the dashboard can
# actually distinguish sub-5ms cache hits from multi-second cache misses.
# The library's default buckets start at 0.1s, which would lump them together.
# ===============================
_LATENCY_BUCKETS = (
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1,
    0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
)
_instrumentator = Instrumentator()
_instrumentator.add(metrics.default(latency_highr_buckets=_LATENCY_BUCKETS))
_instrumentator.instrument(app).expose(app)


# ===============================
# SCHEMAS
# ===============================
class ChatRequest(BaseModel):
    question: str

class ExportRequest(BaseModel):
    question: str
    limit: int | None = None


class Timing(BaseModel):
    intent_ms: float
    sql_ms: float
    total_ms: float
    cache_hit: bool


class ChatResponse(BaseModel):
    answer: str
    sql: str | None = None
    timing: Timing | None = None
    error: str | None = None


# ===============================
# ROUTES — utility
# ===============================
@app.get("/")
def home():
    return {"message": "Backend is running!"}


@app.get("/usage")
def usage():
    """Current AI-query budget + rate-limit config. Handy for confirming the
    cost protection is active and how much daily budget is left."""
    return budget_status()


@app.get("/health")
def health():
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  (SELECT COUNT(*) FROM floats)                  AS floats,
                  (SELECT COUNT(*) FROM float_cycles)            AS cycles,
                  (SELECT COUNT(*) FROM float_measurements_flat) AS measurements,
                  (SELECT MAX(juld) FROM float_measurements_flat)AS last_obs
            """)
            row = cur.fetchone()
        conn.close()
        return {
            "status": "ok",
            "floats": row[0],
            "cycles": row[1],
            "measurements": row[2],
            "last_observation": str(row[3]) if row[3] else None,
        }
    except Exception as e:
        logger.error("Health check failed: %s", e)
        raise HTTPException(status_code=503, detail=str(e))


# ===============================
# ROUTES — float trajectory
# ===============================
@app.get("/float/{platform_number}/track")
def float_track(platform_number: str):
    """Return ordered (cycle, date, lat, lon) for a single float — use for map track."""
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cycle_number, juld, latitude, longitude
                FROM float_cycles
                WHERE platform_number = %s
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
                ORDER BY cycle_number
                """,
                (platform_number,),
            )
            rows = cur.fetchall()
        conn.close()
        if not rows:
            raise HTTPException(status_code=404, detail=f"No track data for float {platform_number}")
        return {
            "platform_number": platform_number,
            "points": [
                {"cycle": r[0], "date": str(r[1]), "latitude": r[2], "longitude": r[3]}
                for r in rows
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Track fetch failed for %s: %s", platform_number, e)
        raise HTTPException(status_code=500, detail=str(e))


# ===============================
# ROUTES — vertical profile
# ===============================
@app.get("/profile/{platform_number}/{cycle_number}")
def vertical_profile(platform_number: str, cycle_number: int):
    """Return depth-vs-metric array for a single float cycle (T/S/O2/Chl vs pressure)."""
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pressure, temperature, salinity, doxy, chla, bbp700, ph_in_situ_total, direction
                FROM float_measurements_flat
                WHERE platform_number = %s
                  AND cycle_number = %s
                  AND pressure IS NOT NULL
                ORDER BY pressure
                """,
                (platform_number, cycle_number),
            )
            rows = cur.fetchall()
        conn.close()
        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"No profile data for float {platform_number} cycle {cycle_number}",
            )
        return {
            "platform_number": platform_number,
            "cycle_number": cycle_number,
            "levels": [
                {
                    "pressure": r[0],
                    "temperature": r[1],
                    "salinity": r[2],
                    "doxy": r[3],
                    "chla": r[4],
                    "bbp700": r[5],
                    "ph": r[6],
                    "direction": r[7],
                }
                for r in rows
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Profile fetch failed for %s/%s: %s", platform_number, cycle_number, e)
        raise HTTPException(status_code=500, detail=str(e))


# ===============================
# ROUTES — CSV export
# ===============================
def _build_export_sql(question: str):
    """Shared helper: extract intent → build explore-mode SQL (no LIMIT)."""
    from chat_main_optimised import (
        extract_intent_llm, validate_intent, normalize_intent,
        plan_filters, plan_aggregation, plan_grouping,
        detect_query_mode, validate_metric, build_query_plan,
        generate_sql, validate_sql, _METRIC_MAP,
    )
    intent, _, _ = extract_intent_llm(question)
    validate_intent(intent)
    intent = normalize_intent(intent, question)
    intent["_query_mode"] = detect_query_mode(question)
    validate_metric(intent)
    filters   = plan_filters(intent)
    agg       = plan_aggregation(intent)
    grouping  = plan_grouping(intent)
    plan      = build_query_plan(intent, filters, agg, grouping)
    plan["query_mode"]   = "explore"
    plan["aggregation"]  = {"apply": False, "type": None}
    sql = generate_sql(plan).replace("\nLIMIT 15", "").replace(" LIMIT 15", "")
    validate_sql(sql)
    col = _METRIC_MAP[intent["metric"]]
    metric = intent["metric"]
    return sql, col, metric


@app.post("/export/count", dependencies=[Depends(rate_guard)])
def export_count(request: ChatRequest):
    """Return row count + estimated file size for the given NL question before downloading."""
    try:
        sql, col, metric = _build_export_sql(request.question)
        count_sql = f"SELECT COUNT(*) FROM ({sql}) _sub"
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(count_sql)
            count = cur.fetchone()[0]
        conn.close()
        estimated_mb = round((count * 6 * 55) / (1024 * 1024), 1)
        return {"count": count, "estimated_mb": estimated_mb, "metric": col}
    except Exception as e:
        logger.error("Export count failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/export", dependencies=[Depends(rate_guard)])
def export_csv(request: ExportRequest):
    """Run the same NL query as /chat_optimised but return raw data as a CSV download."""
    from chat_main_optimised import execute_sql
    try:
        sql, col, metric = _build_export_sql(request.question)
        if request.limit and request.limit > 0:
            sql = sql + f"\nLIMIT {int(request.limit)}"
        rows, _, _ = execute_sql(sql)

        headers = ["latitude", "longitude", "juld", "pressure", "platform_number", col]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        output.seek(0)

        filename = f"argo_{metric}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error("Export failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/refresh_data")
def refresh_data():
    return {
        "status": "disabled",
        "message": "Data refresh is disabled in demo deployment. Data is preloaded.",
    }


# ===============================
# CHAT — optimised (primary)
# ===============================
@app.post("/chat_optimised", response_model=ChatResponse, dependencies=[Depends(rate_guard)])
def chat_optimised_endpoint(request: ChatRequest):
    logger.info("chat_optimised: %r", request.question)
    try:
        result = chat_main_optimised(request.question)
        return {
            "answer": result["answer"],
            "sql": result.get("sql"),
            "timing": result["timing"],
            "error": None,
        }
    except Exception as e:
        logger.error("chat_optimised error: %s", e)
        return {"answer": "", "sql": None, "timing": None, "error": str(e)}


# ===============================
# CHAT — unoptimised (legacy)
# ===============================
@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(rate_guard)])
def chat_endpoint(request: ChatRequest):
    logger.info("chat: %r", request.question)
    try:
        result = chat_main(request.question)
        return {
            "answer": result["answer"],
            "sql": result.get("sql"),
            "timing": result["timing"],
            "error": None,
        }
    except Exception as e:
        logger.error("chat error: %s", e)
        return {"answer": "", "sql": None, "timing": None, "error": str(e)}
