"""
Export a curated subset of local PostgreSQL data as a SQL file for Neon.

Target: ~350K rows from float_measurements_flat, covering every sample query
pattern in the frontend (all regions, depths, bio params, time ranges).
Also exports floats (262 rows) and float_cycles (33K rows) for health/track endpoints.

Output: argo_neon.sql  — upload via Neon SQL editor or:
    psql "<your-neon-connection-string>" -f argo_neon.sql
"""

import os
import sys
from datetime import datetime
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DB_PARAMS = {
    "host":     os.getenv("DB_HOST", "127.0.0.1"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
}

OUTPUT_FILE = "argo_neon.sql"

# ---------------------------------------------------------------------------
# Curated segments — (label, WHERE clause, row limit)
# Each segment is independently LIMITed; duplicates are removed by tracking
# seen row IDs across segments.
# ---------------------------------------------------------------------------
SEGMENTS = [
    # ── Arabian Sea (8-25N, 50-77E) — 7.5M rows available ───────────────────
    # Bio params first (highest priority — sparse across the dataset)
    ("Arabian Sea — bio params (doxy/chla/ph/bbp700)",
     "latitude BETWEEN 8 AND 25 AND longitude BETWEEN 50 AND 77"
     " AND (doxy IS NOT NULL OR chla IS NOT NULL OR ph_in_situ_total IS NOT NULL OR bbp700 IS NOT NULL)",
     300_000),

    ("Arabian Sea — shallow 0-200m",
     "latitude BETWEEN 8 AND 25 AND longitude BETWEEN 50 AND 77 AND pressure < 200",
     80_000),

    ("Arabian Sea — mid 200-1000m (covers 450-550m combined query)",
     "latitude BETWEEN 8 AND 25 AND longitude BETWEEN 50 AND 77"
     " AND pressure BETWEEN 200 AND 1000",
     80_000),

    ("Arabian Sea — deep 1000m+ (covers 500-2000m combined query)",
     "latitude BETWEEN 8 AND 25 AND longitude BETWEEN 50 AND 77 AND pressure > 1000",
     80_000),

    # ── Bay of Bengal (5-22N, 77-100E) — 3.7M rows available ───────────────
    ("Bay of Bengal — all depths (covers 200-800m combined query)",
     "latitude BETWEEN 5 AND 22 AND longitude BETWEEN 77 AND 100",
     280_000),

    # ── Andaman Sea (5-18N, 92-100E) — 228K available (take all) ───────────
    ("Andaman Sea — full coverage",
     "latitude BETWEEN 5 AND 18 AND longitude BETWEEN 92 AND 100",
     230_000),

    # ── Equatorial + mid Indian Ocean ───────────────────────────────────────
    ("Equatorial Indian Ocean (0-10N, 60-90E)",
     "latitude BETWEEN 0 AND 10 AND longitude BETWEEN 60 AND 90",
     80_000),

    ("Mid Indian Ocean (0-5N, 50-77E) — fills gaps",
     "latitude BETWEEN 0 AND 5 AND longitude BETWEEN 50 AND 77",
     50_000),

    # ── Southern Indian Ocean (for Indian Ocean max-temp query) ─────────────
    ("Southern Indian Ocean (-30 to 0N, 40-110E)",
     "latitude BETWEEN -30 AND 0 AND longitude BETWEEN 40 AND 110",
     80_000),

    # ── Coordinate boxes ────────────────────────────────────────────────────
    ("Coord box — lat 10-15, lon 65-75",
     "latitude BETWEEN 10 AND 15 AND longitude BETWEEN 65 AND 75",
     80_000),

    ("Coord box — lat 12-18, lon 60-75 (covers 300m combined query)",
     "latitude BETWEEN 12 AND 18 AND longitude BETWEEN 60 AND 75",
     80_000),

    # ── Time-based (Indian Ocean scope) ─────────────────────────────────────
    ("Year 2024 — all rows (only 5,415 exist)",
     "juld >= '2024-01-01' AND juld < '2025-01-01'",
     6_000),

    ("2018-2020 — Indian Ocean sample",
     "juld >= '2018-01-01' AND juld < '2021-01-01'"
     " AND latitude BETWEEN -60 AND 30 AND longitude BETWEEN 20 AND 120",
     120_000),

    ("June 2026 — all 91K rows",
     "juld >= '2026-06-01' AND juld < '2026-07-01'",
     92_000),

    ("Dissolved oxygen since 2022 — all ~3.7K rows",
     "juld >= '2022-01-01' AND doxy IS NOT NULL",
     5_000),

    ("April-May 2026 (extra recent coverage)",
     "juld >= '2026-04-01' AND juld < '2026-06-01'",
     10_000),
]

# ---------------------------------------------------------------------------
# Columns exported for the flat table (id excluded — Neon auto-generates)
# ---------------------------------------------------------------------------
FLAT_COLS = [
    "platform_number", "cycle_number", "juld", "latitude", "longitude",
    "pressure", "temperature", "salinity", "profile_type", "direction",
    "doxy", "chla", "bbp700", "ph_in_situ_total",
    "pi_name", "project_name", "institution",
]

FLOAT_CYCLES_COLS = [
    "platform_number", "cycle_number", "juld", "latitude", "longitude",
    "position_qc", "data_mode", "source",
]

SCHEMA_SQL = """
-- ============================================================
-- ARGO curated dataset for Neon
-- Generated: {ts}
-- Rows: ~350K from float_measurements_flat (curated)
--       262 floats, 33K float_cycles (full)
-- ============================================================

DROP TABLE IF EXISTS float_measurements_flat CASCADE;
DROP TABLE IF EXISTS float_cycles CASCADE;
DROP TABLE IF EXISTS floats CASCADE;

CREATE TABLE floats (
    platform_number TEXT PRIMARY KEY
);

CREATE TABLE float_cycles (
    id              SERIAL PRIMARY KEY,
    platform_number TEXT,
    cycle_number    INTEGER,
    juld            TIMESTAMP,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    position_qc     TEXT,
    data_mode       TEXT,
    source          TEXT,
    UNIQUE (platform_number, cycle_number)
);

CREATE TABLE float_measurements_flat (
    id               SERIAL PRIMARY KEY,
    platform_number  TEXT,
    cycle_number     INTEGER,
    juld             TIMESTAMP,
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    pressure         DOUBLE PRECISION,
    temperature      DOUBLE PRECISION,
    salinity         DOUBLE PRECISION,
    profile_type     TEXT,
    direction        TEXT,
    doxy             DOUBLE PRECISION,
    chla             DOUBLE PRECISION,
    bbp700           DOUBLE PRECISION,
    ph_in_situ_total DOUBLE PRECISION,
    pi_name          VARCHAR(200),
    project_name     VARCHAR(200),
    institution      VARCHAR(100)
);
"""

INDEX_SQL = """
CREATE INDEX idx_fmf_lat      ON float_measurements_flat (latitude);
CREATE INDEX idx_fmf_lon      ON float_measurements_flat (longitude);
CREATE INDEX idx_fmf_juld     ON float_measurements_flat (juld);
CREATE INDEX idx_fmf_pressure ON float_measurements_flat (pressure);
CREATE INDEX idx_fmf_platform ON float_measurements_flat (platform_number);
"""


def _copy_val(v) -> str:
    """Format a Python value for PostgreSQL COPY text format."""
    if v is None:
        return "\\N"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    s = str(v)
    # Escape backslash, tab, newline, carriage return
    s = s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    return s


def export_table_copy(cur, f, table: str, columns: list, where: str = "", limit: int = 0, seen_ids: set = None):
    """Execute a query and write rows in COPY text format."""
    col_sql = ", ".join(columns)
    sql = f"SELECT id, {col_sql} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY juld DESC NULLS LAST"
    if limit:
        sql += f" LIMIT {limit}"

    cur.execute(sql)
    written = 0
    for row in cur:
        row_id = row[0]
        if seen_ids is not None:
            if row_id in seen_ids:
                continue
            seen_ids.add(row_id)
        values = "\t".join(_copy_val(v) for v in row[1:])
        f.write(values + "\n")
        written += 1
    return written


def export_small_table(cur, f, table: str, columns: list):
    """Export an entire small table (no dedup needed)."""
    col_sql = ", ".join(columns)
    cur.execute(f"SELECT {col_sql} FROM {table}")
    rows = cur.fetchall()
    col_list = ", ".join(columns)
    f.write(f"\nCOPY {table} ({col_list}) FROM stdin;\n")
    for row in rows:
        f.write("\t".join(_copy_val(v) for v in row) + "\n")
    f.write("\\.\n")
    return len(rows)


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True
    cur = conn.cursor()

    print(f"Writing to {OUTPUT_FILE} ...")
    seen_ids: set = set()
    grand_total = 0

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        # --- Schema ---
        f.write(SCHEMA_SQL.format(ts=datetime.now().strftime("%Y-%m-%d %H:%M")))

        # --- floats table (262 rows) ---
        n = export_small_table(cur, f, "floats", ["platform_number"])
        print(f"  floats: {n:,} rows")

        # --- float_cycles table (33K rows) ---
        n = export_small_table(cur, f, "float_cycles", FLOAT_CYCLES_COLS)
        print(f"  float_cycles: {n:,} rows")

        # --- float_measurements_flat — curated segments ---
        col_list = ", ".join(FLAT_COLS)
        f.write(f"\nCOPY float_measurements_flat ({col_list}) FROM stdin;\n")

        for label, where, limit in SEGMENTS:
            n = export_table_copy(cur, f, "float_measurements_flat",
                                  FLAT_COLS, where, limit, seen_ids)
            grand_total += n
            print(f"  [{label}] -> {n:,} new rows  (running total: {grand_total:,})")

        f.write("\\.\n")

        # --- Indexes ---
        f.write(INDEX_SQL)

    cur.close()
    conn.close()

    size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print(f"\nDone! {grand_total:,} unique rows in {OUTPUT_FILE} ({size_mb:.1f} MB)")
    print("Upload with:")
    print('  psql "<your-neon-connection-string>" -f argo_neon.sql')


if __name__ == "__main__":
    main()
