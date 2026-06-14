import os
import psycopg2
import xarray as xr
import numpy as np
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}

SPROF_DIR = "data_downloads/data_downloads_sprof"
BIO_PARAMS = ["DOXY", "CHLA", "BBP700", "PH_IN_SITU_TOTAL"]

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

# ================= PLATFORM NUMBER (FINAL) =================
def get_platform_number(ds, filename):
    # 1. Variable
    if "PLATFORM_NUMBER" in ds.variables:
        v = ds["PLATFORM_NUMBER"].values
        return safe_str(v.flat[0])

    # 2. Global attribute
    v = ds.attrs.get("PLATFORM_NUMBER")
    if v:
        return safe_str(v)

    # 3. Filename fallback (ARGO standard)
    base = os.path.basename(filename)
    if "_" in base:
        return base.split("_")[0]

    return None

# ================= FLOAT MASTER =================
def ensure_float_exists(conn, platform_number):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM floats WHERE platform_number = %s",
            (platform_number,)
        )
        if cur.fetchone():
            return

        cur.execute(
            "INSERT INTO floats (platform_number) VALUES (%s)",
            (platform_number,)
        )

# ================= CYCLE =================
def get_or_create_cycle(conn, platform_number, cycle_number, juld):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM float_cycles
            WHERE platform_number = %s
              AND cycle_number = %s
            """,
            (platform_number, int(cycle_number))
        )
        r = cur.fetchone()
        if r:
            return r[0]

        juld_ts = juld_to_timestamp(juld)

        cur.execute(
            """
            INSERT INTO float_cycles
            (platform_number, cycle_number, juld, latitude, longitude, source)
            VALUES (%s,%s,%s,NULL,NULL,'PROFILE_ONLY')
            RETURNING id
            """,
            (platform_number, int(cycle_number), juld_ts)
        )
        return cur.fetchone()[0]

# ================= PROFILE HELPERS =================
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

# ================= DATA MODE =================
def derive_data_modes(ds, n_profiles):
    if "PARAMETER_DATA_MODE" not in ds:
        return [None] * n_profiles

    pdm = ds["PARAMETER_DATA_MODE"].values

    def worst(modes):
        s = {safe_str(x) for x in modes if safe_str(x)}
        if "D" in s:
            return "D"
        if "A" in s:
            return "A"
        if "R" in s:
            return "R"
        return None

    return [worst(pdm[p]) for p in range(pdm.shape[0])]

# ================= SPROF =================
def process_sprof_file(conn, path):
    print(f"\n📂 Processing {os.path.basename(path)}")

    ds = xr.open_dataset(path, decode_times=False, engine="netcdf4")

    platform_number = get_platform_number(ds, path)
    if not platform_number:
        raise ValueError(f"PLATFORM_NUMBER not found in {path}")

    ensure_float_exists(conn, platform_number)

    pres = ds["PRES"].values
    temp = ds["TEMP"].values if "TEMP" in ds else None
    psal = ds["PSAL"].values if "PSAL" in ds else None

    pres_qc = ds["PRES_QC"].values
    temp_qc = ds["TEMP_QC"].values if "TEMP_QC" in ds else None
    psal_qc = ds["PSAL_QC"].values if "PSAL_QC" in ds else None

    cycles = ds["CYCLE_NUMBER"].values
    julds = ds["JULD"].values
    directions = ds["DIRECTION"].values if "DIRECTION" in ds else [None] * pres.shape[0]

    bio = {p: ds[p].values for p in BIO_PARAMS if p in ds}
    bio_qc = {p: ds[f"{p}_QC"].values for p in BIO_PARAMS if f"{p}_QC" in ds}

    n_profiles, n_levels = pres.shape
    data_modes = derive_data_modes(ds, n_profiles)

    print(f"   Profiles={n_profiles}, Levels={n_levels}")

    for p in range(n_profiles):
        cycle_id = get_or_create_cycle(
            conn,
            platform_number,
            cycles[p],
            julds[p]
        )

        direction = safe_str(directions[p])
        data_mode = data_modes[p]

        existing = get_existing_profile_id(conn, cycle_id, "SPROF", direction)

        if existing:
            profile_id = existing
            delete_profile_levels(conn, profile_id)
            update_profile(conn, profile_id, data_mode)
            action = "UPDATED"
        else:
            profile_id = insert_profile(
                conn, cycle_id, "SPROF", direction, data_mode
            )
            action = "INSERTED"

        rows = []
        for z in range(n_levels):
            pres_val = safe_float(pres[p, z])
            if pres_val is None:
                continue

            qc = {
                "pres_qc": safe_str(pres_qc[p, z]),
                "temp_qc": safe_str(temp_qc[p, z]) if temp_qc is not None else None,
                "psal_qc": safe_str(psal_qc[p, z]) if psal_qc is not None else None
            }

            for bp in bio:
                qc[bp.lower()] = safe_float(bio[bp][p, z])
                if bp in bio_qc:
                    qc[f"{bp.lower()}_qc"] = safe_str(bio_qc[bp][p, z])

            rows.append({
                "pres": pres_val,
                "temp": safe_float(temp[p, z]) if temp is not None else None,
                "psal": safe_float(psal[p, z]) if psal is not None else None,
                "qc": qc
            })

        if rows:
            insert_profile_levels(conn, profile_id, rows)

        print(f"   🔁 Cycle {int(cycles[p])} | {action} | {len(rows)} levels")

    ds.close()

# ================= RUN =================
def run():
    conn = connect_db()
    try:
        for f in sorted(os.listdir(SPROF_DIR)):
            if f.endswith(".nc"):
                process_sprof_file(conn, os.path.join(SPROF_DIR, f))
    finally:
        conn.close()
        print("\n🔒 DB connection closed")

if __name__ == "__main__":
    run()
