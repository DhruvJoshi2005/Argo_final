import os
import psycopg2
import xarray as xr
import numpy as np
from datetime import datetime, timedelta

DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "argo_final",
    "user": "postgres",
    "password": "Dhruv@2005"
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


def safe_float(v, default=0.0):
    try:
        return float(v)
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


def parse_juld_fallback(cycle_number):
    return datetime(1950, 1, 1) + timedelta(days=int(cycle_number))


# ================= RTRAJ PROCESSOR =================
def process_rtraj_file(conn, filepath):
    filename = os.path.basename(filepath)
    ds = xr.open_dataset(filepath, decode_times=False, engine="netcdf4")

    try:
        platform_number = get_platform_number(ds, filename)
        ensure_float_exists(conn, platform_number)

        cycle_arr = ds["CYCLE_NUMBER"].values

        # robust cycle extraction
        cycles = sorted({
            int(c)
            for c in cycle_arr
            if c is not None and not np.ma.is_masked(c) and int(c) >= 0
        })

        lat_arr = ds["LATITUDE"].values if "LATITUDE" in ds else None
        lon_arr = ds["LONGITUDE"].values if "LONGITUDE" in ds else None
        qc_arr  = ds["POSITION_QC"].values if "POSITION_QC" in ds else None
        dm_arr  = ds["DATA_MODE"].values if "DATA_MODE" in ds else None

        n_cycles = len(lat_arr) if lat_arr is not None else 0

        for cycle in cycles:
            cycle_index = min(cycle, n_cycles - 1) if n_cycles > 0 else 0

            lat = safe_float(safe_array_value(lat_arr, cycle_index), 0.0)
            lon = safe_float(safe_array_value(lon_arr, cycle_index), 0.0)

            juld = parse_juld_fallback(cycle)

            position_qc = safe_str(safe_array_value(qc_arr, cycle_index))
            data_mode   = safe_str(safe_array_value(dm_arr, cycle_index))

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO float_cycles
                    (platform_number, cycle_number, juld, latitude, longitude, position_qc, data_mode)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (platform_number, cycle_number)
                    DO NOTHING
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

        print(f"✅ RTRAJ ingested: {platform_number}")

    except Exception as e:
        print(f"❌ RTRAJ failed ({filename}): {e}")

    finally:
        ds.close()


# ================= RUNNER =================
def run_rtraj_ingestion():
    conn = connect_db()
    try:
        for fname in sorted(os.listdir(RTRAJ_DIR)):
            if fname.endswith(".nc"):
                process_rtraj_file(conn, os.path.join(RTRAJ_DIR, fname))
    finally:
        conn.close()
        print("🔒 DB connection closed")


if __name__ == "__main__":
    run_rtraj_ingestion()