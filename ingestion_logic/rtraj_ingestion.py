import logging
import os
import psycopg2
import xarray as xr
import numpy as np
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

RTRAJ_DIR = "data_downloads/data_downloads_Rtraj"


# ================= DB =================
def connect_db():
    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True
    return conn


def ensure_float_exists(conn, platform_number):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO floats (platform_number) VALUES (%s) ON CONFLICT DO NOTHING",
            (platform_number,)
        )


# ================= HELPERS =================
def get_platform_number(ds, filename):
    return ds.attrs.get("PLATFORM_NUMBER", filename.split("_")[0])


def safe_float(v, default=None):
    # NOTE: previously defaulted to 0.0 and had no NaN/inf guard, so masked
    # LATITUDE/LONGITUDE fill values could silently become 0.0 ("null island")
    # instead of a missing value.
    try:
        v = float(v)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except Exception:
        return default


def safe_str(v):
    if isinstance(v, (bytes, np.bytes_)):
        return v.decode("utf-8", errors="ignore").strip()
    return str(v).strip() if v is not None else None


def safe_array_value(arr, idx, default=None):
    if arr is None:
        return default
    if idx < 0 or idx >= len(arr):
        return default
    return arr[idx]


def safe_position(lat, lon, qc):
    """Only trust a position when QC marks it good ('1') or probably good ('2')."""
    lat = safe_float(lat)
    lon = safe_float(lon)
    qc = safe_str(qc)
    if lat is None or lon is None or qc not in ("1", "2"):
        return None, None
    return lat, lon


_ARGO_MIN_DATE = datetime(2000, 1, 1)

def safe_juld(juld_raw):
    """Convert raw JULD (days since 1950-01-01) to timestamp.
    Returns None for NaN/inf, zero, or results before 2000 (bad source data)."""
    try:
        j = float(juld_raw)
        if np.isnan(j) or np.isinf(j) or j <= 0:
            return None
        ts = datetime(1950, 1, 1) + timedelta(days=j)
        return ts if ts >= _ARGO_MIN_DATE else None
    except Exception:
        return None


def safe_cycle_int(c):
    """Convert a CYCLE_NUMBER value to int, returning None for NaN/inf/bad values."""
    try:
        v = float(c)
        return None if (np.isnan(v) or np.isinf(v)) else int(v)
    except Exception:
        return None


# ================= RTRAJ PROCESSOR =================
def process_rtraj_file(conn, filepath):
    filename = os.path.basename(filepath)
    ds = xr.open_dataset(filepath, decode_times=False, engine="netcdf4")

    try:
        platform_number = get_platform_number(ds, filename)
        ensure_float_exists(conn, platform_number)

        cycle_arr = ds["CYCLE_NUMBER"].values

        cycles = sorted({
            v for c in cycle_arr
            if c is not None and not np.ma.is_masked(c)
            for v in [safe_cycle_int(c)]
            if v is not None and v >= 0
        })

        lat_arr = ds["LATITUDE"].values if "LATITUDE" in ds else None
        lon_arr = ds["LONGITUDE"].values if "LONGITUDE" in ds else None
        qc_arr  = ds["POSITION_QC"].values if "POSITION_QC" in ds else None
        dm_arr  = ds["DATA_MODE"].values if "DATA_MODE" in ds else None
        juld_arr = ds["JULD"].values if "JULD" in ds else None

        # LATITUDE/LONGITUDE/POSITION_QC/JULD are indexed by N_MEASUREMENT (one row
        # per trajectory/GPS fix), NOT by cycle — CYCLE_NUMBER repeats across many
        # rows per cycle, so a cycle number can't be used as a direct array index
        # into them. Walk all measurement rows once and keep the last QC-good
        # ('1'/'2') position (and its JULD) seen per cycle (rows are chronological).
        measurement_aligned = (
            lat_arr is not None and lon_arr is not None
            and len(lat_arr) == len(cycle_arr) and len(lon_arr) == len(cycle_arr)
        )
        juld_per_measurement = juld_arr is not None and len(juld_arr) == len(cycle_arr)
        # DATA_MODE is documented as per-cycle in the Argo trajectory spec, unlike
        # LATITUDE/LONGITUDE — only treat it as per-measurement if its length
        # actually matches, otherwise leave it unset rather than guess.
        dm_per_measurement = dm_arr is not None and len(dm_arr) == len(cycle_arr)

        per_cycle = {}  # cycle_number -> {"lat", "lon", "position_qc", "data_mode", "juld"}
        for i, c in enumerate(cycle_arr):
            if c is None or np.ma.is_masked(c):
                continue
            c = safe_cycle_int(c)
            if c is None or c < 0:
                continue
            entry = per_cycle.setdefault(
                c, {"lat": None, "lon": None, "position_qc": None, "data_mode": None, "juld": None}
            )

            if measurement_aligned:
                lat, lon = safe_position(
                    safe_array_value(lat_arr, i),
                    safe_array_value(lon_arr, i),
                    safe_array_value(qc_arr, i) if qc_arr is not None else None,
                )
                if lat is not None and lon is not None:
                    entry["lat"] = lat
                    entry["lon"] = lon
                    entry["position_qc"] = safe_str(safe_array_value(qc_arr, i)) if qc_arr is not None else None
                    if juld_per_measurement:
                        entry["juld"] = safe_juld(safe_array_value(juld_arr, i))

            if dm_per_measurement:
                dm = safe_str(safe_array_value(dm_arr, i))
                if dm:
                    entry["data_mode"] = dm

        for cycle in cycles:
            entry = per_cycle.get(cycle, {})
            lat = entry.get("lat")
            lon = entry.get("lon")
            position_qc = entry.get("position_qc")
            data_mode = entry.get("data_mode")

            juld = entry.get("juld")

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO float_cycles
                    (platform_number, cycle_number, juld, latitude, longitude, position_qc, data_mode)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (platform_number, cycle_number) DO UPDATE
                    SET latitude  = COALESCE(float_cycles.latitude, EXCLUDED.latitude),
                        longitude = COALESCE(float_cycles.longitude, EXCLUDED.longitude)
                    WHERE float_cycles.latitude IS NULL OR float_cycles.longitude IS NULL
                    """,
                    (
                        platform_number,
                        cycle,
                        juld,
                        lat,
                        lon,
                        position_qc,
                        data_mode,
                    )
                )

        logger.info("RTRAJ ingested: %s", platform_number)
        return platform_number

    except Exception as e:
        logger.error("RTRAJ failed (%s): %s", filename, e)
        return None

    finally:
        ds.close()


# ================= RUNNER =================
def run_rtraj_ingestion():
    platforms = set()
    conn = connect_db()
    try:
        for fname in sorted(os.listdir(RTRAJ_DIR)):
            if fname.endswith(".nc"):
                pn = process_rtraj_file(conn, os.path.join(RTRAJ_DIR, fname))
                if pn:
                    platforms.add(pn)
    finally:
        conn.close()
        logger.info("RTRAJ DB connection closed")
    return platforms


if __name__ == "__main__":
    run_rtraj_ingestion()