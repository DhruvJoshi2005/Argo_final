import os
import psycopg2
import xarray as xr
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ---------------- DB CONFIG ----------------
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

TECH_DIR = "data_downloads/data_downloads_tech"


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
    if key in ds.attrs:
        return safe_str(ds.attrs[key])
    if key in ds.variables:
        arr = ds[key].values
        if isinstance(arr, np.ndarray):
            if arr.shape == ():      # scalar
                return safe_str(arr.item())
            if arr.size > 0:
                return safe_str(arr.flat[0])
    return None


def get_platform_number(ds, filename):
    return (
        get_nc_value(ds, "PLATFORM_NUMBER")
        or filename.split("_")[0]
    )


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


# ---------------- TECH INSERT ----------------
def upsert_float_tech(conn, data):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO float_tech (
                platform_number,
                date_creation,
                date_update,
                data_centre,
                data_type,
                format_version,
                handbook_version,
                technical_parameter_name,
                technical_parameter_value,
                cycle_number
            )
            VALUES (
                %(platform_number)s,
                %(date_creation)s,
                %(date_update)s,
                %(data_centre)s,
                %(data_type)s,
                %(format_version)s,
                %(handbook_version)s,
                %(technical_parameter_name)s,
                %(technical_parameter_value)s,
                %(cycle_number)s
            )
            ON CONFLICT (platform_number)
            DO UPDATE SET
                date_creation = EXCLUDED.date_creation,
                date_update   = EXCLUDED.date_update,
                data_centre   = EXCLUDED.data_centre,
                data_type     = EXCLUDED.data_type,
                format_version = EXCLUDED.format_version,
                handbook_version = EXCLUDED.handbook_version,
                technical_parameter_name = EXCLUDED.technical_parameter_name,
                technical_parameter_value = EXCLUDED.technical_parameter_value,
                cycle_number = EXCLUDED.cycle_number
            """
        , data)


# ---------------- TECH PROCESSOR ----------------
def process_tech_file(conn, filepath):
    filename = os.path.basename(filepath)
    ds = xr.open_dataset(filepath, decode_times=False)

    try:
        platform_number = get_platform_number(ds, filename)
        if not platform_number:
            raise ValueError("PLATFORM_NUMBER missing")

        ensure_float_exists(conn, platform_number)

        names = []
        values = []
        cycles = []

        if "TECHNICAL_PARAMETER_NAME" in ds:
            names = [safe_str(x) for x in ds["TECHNICAL_PARAMETER_NAME"].values]

        if "TECHNICAL_PARAMETER_VALUE" in ds:
            values = [safe_str(x) for x in ds["TECHNICAL_PARAMETER_VALUE"].values]

        if "CYCLE_NUMBER" in ds:
            for c in ds["CYCLE_NUMBER"].values:
                try:
                    c = int(c)
                    cycles.append(c)
                except Exception:
                    cycles.append(None)

        data = {
            "platform_number": platform_number,
            "date_creation": parse_argodate(get_nc_value(ds, "DATE_CREATION")),
            "date_update": parse_argodate(get_nc_value(ds, "DATE_UPDATE")),
            "data_centre": get_nc_value(ds, "DATA_CENTRE"),
            "data_type": get_nc_value(ds, "DATA_TYPE"),
            "format_version": get_nc_value(ds, "FORMAT_VERSION"),
            "handbook_version": get_nc_value(ds, "HANDBOOK_VERSION"),
            "technical_parameter_name": names,
            "technical_parameter_value": values,
            "cycle_number": cycles
        }

        upsert_float_tech(conn, data)
        conn.commit()
        print(f"✅ TECH ingested: {platform_number}")
        return platform_number

    except Exception as e:
        conn.rollback()
        print(f"❌ TECH failed ({filename}): {e}")
        return None

    finally:
        ds.close()


# ---------------- RUNNER ----------------
def run_tech_ingestion():
    platforms = set()
    conn = connect_db()
    try:
        for fname in sorted(os.listdir(TECH_DIR)):
            if fname.endswith(".nc"):
                pn = process_tech_file(conn, os.path.join(TECH_DIR, fname))
                if pn:
                    platforms.add(pn)
    finally:
        conn.close()
        print("🔒 DB connection closed")
    return platforms


if __name__ == "__main__":
    run_tech_ingestion()