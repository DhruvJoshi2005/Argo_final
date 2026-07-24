import logging
import os
import psycopg2
import xarray as xr
import numpy as np
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ================= CONFIG =================
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

PROF_DIR = "data_downloads/data_downloads_prof"


# ================= DB =================
def connect_db():
    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True
    return conn


# ================= HELPERS =================
def safe_float(v):
    try:
        v = float(v)
        if np.isnan(v) or np.isinf(v):
            return None
        return v
    except Exception:
        return None


def safe_str(v):
    if isinstance(v, (bytes, np.bytes_)):
        return v.decode("utf-8", errors="ignore").strip()
    if v is None:
        return None
    s = str(v).strip()
    return s if s not in ("", "nan", "None") else None


def juld_to_timestamp(juld):
    try:
        j = float(juld)
        if np.isnan(j):
            return None
        return datetime(1950, 1, 1) + timedelta(days=j)
    except Exception:
        return None


def safe_position(lat, lon, qc):
    """Only trust a position when QC marks it good ('1') or probably good ('2')."""
    lat = safe_float(lat)
    lon = safe_float(lon)
    qc = safe_str(qc)
    if lat is None or lon is None or qc not in ("1", "2"):
        return None, None
    return lat, lon


# ================= DB UTILS =================
def ensure_float_exists(conn, platform_number):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO floats (platform_number) VALUES (%s) ON CONFLICT DO NOTHING",
            (platform_number,)
        )


def get_or_create_cycle(conn, platform_number, cycle_number, juld, lat, lon):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, latitude, longitude
            FROM float_cycles
            WHERE platform_number = %s
              AND cycle_number = %s
            """,
            (platform_number, int(cycle_number))
        )
        r = cur.fetchone()
        if r:
            cycle_id, existing_lat, existing_lon = r
            if (existing_lat is None or existing_lon is None) and lat is not None and lon is not None:
                # Backfill a position left NULL by an earlier pass.
                cur.execute(
                    """
                    UPDATE float_cycles
                    SET latitude = %s, longitude = %s
                    WHERE id = %s
                      AND (latitude IS NULL OR longitude IS NULL)
                    """,
                    (lat, lon, cycle_id)
                )
            return cycle_id

        juld_ts = juld_to_timestamp(juld)

        cur.execute(
            """
            INSERT INTO float_cycles
            (platform_number, cycle_number, juld, latitude, longitude, source)
            VALUES (%s,%s,%s,%s,%s,'PROFILE_ONLY')
            RETURNING id
            """,
            (platform_number, int(cycle_number), juld_ts, lat, lon)
        )
        return cur.fetchone()[0]


def get_existing_profile_id(conn, cycle_id, profile_type, direction):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM float_profiles
            WHERE cycle_id = %s
              AND profile_type = %s
              AND direction IS NOT DISTINCT FROM %s
            """,
            (cycle_id, profile_type, direction)
        )
        r = cur.fetchone()
        return r[0] if r else None


def delete_profile_levels(conn, profile_id):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM float_profile_levels WHERE profile_id = %s",
            (profile_id,)
        )


def insert_profile(conn, cycle_id, profile_type, direction, data_mode):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO float_profiles
            (cycle_id, profile_type, direction, data_mode)
            VALUES (%s,%s,%s,%s)
            RETURNING id
            """,
            (cycle_id, profile_type, direction, data_mode)
        )
        return cur.fetchone()[0]


def update_profile(conn, profile_id, data_mode):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE float_profiles
            SET data_mode = %s,
                created_at = now()
            WHERE id = %s
            """,
            (data_mode, profile_id)
        )


def insert_profile_levels(conn, profile_id, rows):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO float_profile_levels
                (profile_id, pres, temp, psal, qc)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    profile_id,
                    r["pres"],
                    r["temp"],
                    r["psal"],
                    json.dumps(r["qc"])
                )
            )


# ================= MAIN PROCESSOR =================
def process_prof_file(conn, path):
    fname = os.path.basename(path)
    logger.info("Processing %s", fname)

    ds = xr.open_dataset(path, decode_times=False, engine="netcdf4")

    platform_number = str(ds.attrs.get("PLATFORM_NUMBER", fname.split("_")[0]))
    ensure_float_exists(conn, platform_number)

    pres = ds["PRES"].values
    temp = ds["TEMP"].values
    psal = ds["PSAL"].values

    pres_qc = ds["PRES_QC"].values
    temp_qc = ds["TEMP_QC"].values
    psal_qc = ds["PSAL_QC"].values

    cycles = ds["CYCLE_NUMBER"].values
    directions = ds["DIRECTION"].values
    data_modes = ds["DATA_MODE"].values
    julds = ds["JULD"].values if "JULD" in ds else None

    # Per-profile position, on the same N_PROF axis as TEMP/PSAL.
    lat_arr = ds["LATITUDE"].values if "LATITUDE" in ds else None
    lon_arr = ds["LONGITUDE"].values if "LONGITUDE" in ds else None
    pos_qc_arr = ds["POSITION_QC"].values if "POSITION_QC" in ds else None

    n_profiles, n_levels = pres.shape
    logger.info("  Profiles=%d, Levels=%d", n_profiles, n_levels)

    for p in range(n_profiles):
        cycle_number = cycles[p]

        lat, lon = safe_position(
            lat_arr[p] if lat_arr is not None else None,
            lon_arr[p] if lon_arr is not None else None,
            pos_qc_arr[p] if pos_qc_arr is not None else None,
        )
        juld = julds[p] if julds is not None else None

        cycle_id = get_or_create_cycle(conn, platform_number, cycle_number, juld, lat, lon)

        direction = safe_str(directions[p])
        data_mode = safe_str(data_modes[p])

        existing_profile_id = get_existing_profile_id(
            conn, cycle_id, "PROF", direction
        )

        if existing_profile_id:
            profile_id = existing_profile_id
            delete_profile_levels(conn, profile_id)
            update_profile(conn, profile_id, data_mode)
            action = "UPDATED"
        else:
            profile_id = insert_profile(
                conn, cycle_id, "PROF", direction, data_mode
            )
            action = "INSERTED"

        rows = []
        for z in range(n_levels):
            pres_val = safe_float(pres[p, z])
            if pres_val is None:
                continue

            rows.append({
                "pres": pres_val,
                "temp": safe_float(temp[p, z]),
                "psal": safe_float(psal[p, z]),
                "qc": {
                    "pres_qc": safe_str(pres_qc[p, z]),
                    "temp_qc": safe_str(temp_qc[p, z]),
                    "psal_qc": safe_str(psal_qc[p, z])
                }
            })

        if rows:
            insert_profile_levels(conn, profile_id, rows)

        logger.debug("  Cycle %d | %s | %d levels", int(cycle_number), action, len(rows))

    ds.close()
    return platform_number


# ================= RUN =================
def run():
    platforms = set()
    conn = connect_db()
    try:
        for f in sorted(os.listdir(PROF_DIR)):
            if f.endswith(".nc"):
                pn = process_prof_file(conn, os.path.join(PROF_DIR, f))
                if pn:
                    platforms.add(pn)
    finally:
        conn.close()
        logger.info("PROF DB connection closed")
    return platforms


if __name__ == "__main__":
    run()