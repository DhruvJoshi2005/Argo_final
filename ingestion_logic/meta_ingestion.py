import os
import psycopg2
import xarray as xr
import numpy as np
from datetime import datetime

# ---------------- DB CONFIG ----------------
DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "argo_final",
    "user": "postgres",
    "password": "Dhruv@2005"
}

META_DIR = "data_downloads/data_downloads_meta"


# ---------------- HELPERS ----------------
def connect_db():
    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = False
    return conn


def safe_str(val):
    if val is None:
        return None
    if isinstance(val, (bytes, np.bytes_)):
        return val.decode("utf-8", errors="ignore").strip()
    if isinstance(val, np.ndarray):
        if val.size == 0:
            return None
        return safe_str(val.item())
    return str(val).strip()


def parse_argodate(val):
    if val is None:
        return None
    val = safe_str(val)
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y%m%d%H%M%S")
    except Exception:
        return None


def get_nc_value(ds, key):
    """
    Safely get value from attrs or variables (scalar or array)
    """
    if key in ds.attrs:
        return safe_str(ds.attrs[key])

    if key in ds.variables:
        arr = ds[key].values
        if isinstance(arr, np.ndarray):
            if arr.shape == ():      # scalar
                return safe_str(arr.item())
            if arr.size > 0:         # array
                return safe_str(arr.flat[0])
    return None


def get_platform_number(ds, filename):
    return (
        get_nc_value(ds, "PLATFORM_NUMBER")
        or filename.split("_")[0]
    )


# ---------------- DB INSERTS ----------------
def ensure_float_exists(conn, platform_number):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO floats (platform_number)
            VALUES (%s)
            ON CONFLICT (platform_number) DO NOTHING
            """,
            (platform_number,)
        )


def upsert_float_meta(conn, data):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO float_meta (
                platform_number,
                project_name,
                pi_name,
                data_centre,
                institution,
                date_creation,
                date_update
            )
            VALUES (
                %(platform_number)s,
                %(project_name)s,
                %(pi_name)s,
                %(data_centre)s,
                %(institution)s,
                %(date_creation)s,
                %(date_update)s
            )
            ON CONFLICT (platform_number)
            DO UPDATE SET
                project_name  = EXCLUDED.project_name,
                pi_name       = EXCLUDED.pi_name,
                data_centre   = EXCLUDED.data_centre,
                institution   = EXCLUDED.institution,
                date_creation = EXCLUDED.date_creation,
                date_update   = EXCLUDED.date_update
            """
        , data)


# ---------------- META PROCESSOR ----------------
def process_meta_file(conn, filepath):
    filename = os.path.basename(filepath)
    ds = xr.open_dataset(filepath, decode_times=False)

    try:
        platform_number = get_platform_number(ds, filename)
        if not platform_number:
            raise ValueError("PLATFORM_NUMBER missing")

        ensure_float_exists(conn, platform_number)

        data = {
            "platform_number": platform_number,
            "project_name": get_nc_value(ds, "PROJECT_NAME"),
            "pi_name": get_nc_value(ds, "PI_NAME"),
            "data_centre": get_nc_value(ds, "DATA_CENTRE"),
            "institution": get_nc_value(ds, "institution"),
            "date_creation": parse_argodate(get_nc_value(ds, "DATE_CREATION")),
            "date_update": parse_argodate(get_nc_value(ds, "DATE_UPDATE"))
        }

        upsert_float_meta(conn, data)
        conn.commit()
        print(f"✅ META ingested: {platform_number}")

    except Exception as e:
        conn.rollback()
        print(f"❌ META failed ({filename}): {e}")

    finally:
        ds.close()


# ---------------- RUNNER ----------------
def run_meta_ingestion():
    conn = connect_db()
    try:
        for fname in sorted(os.listdir(META_DIR)):
            if fname.endswith(".nc"):
                process_meta_file(conn, os.path.join(META_DIR, fname))
    finally:
        conn.close()
        print("🔒 DB connection closed")


if __name__ == "__main__":
    run_meta_ingestion()