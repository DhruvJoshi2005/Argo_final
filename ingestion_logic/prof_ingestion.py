import os
import psycopg2
import xarray as xr
import numpy as np
import json
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


# ================= DB UTILS =================
def get_cycle_id(conn, platform_number, cycle_number):
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
        return r[0] if r else None


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
    print(f"\n📂 Processing {fname}")

    ds = xr.open_dataset(path, decode_times=False, engine="netcdf4")

    platform_number = str(ds.attrs.get("PLATFORM_NUMBER", fname.split("_")[0]))

    pres = ds["PRES"].values
    temp = ds["TEMP"].values
    psal = ds["PSAL"].values

    pres_qc = ds["PRES_QC"].values
    temp_qc = ds["TEMP_QC"].values
    psal_qc = ds["PSAL_QC"].values

    cycles = ds["CYCLE_NUMBER"].values
    directions = ds["DIRECTION"].values
    data_modes = ds["DATA_MODE"].values

    n_profiles, n_levels = pres.shape
    print(f"   Profiles={n_profiles}, Levels={n_levels}")

    for p in range(n_profiles):
        cycle_number = cycles[p]
        cycle_id = get_cycle_id(conn, platform_number, cycle_number)
        if cycle_id is None:
            continue

        direction = safe_str(directions[p])
        data_mode = safe_str(data_modes[p])

        # 🔁 Overwrite-safe logic
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

        print(
            f"   🔁 Cycle {int(cycle_number)} | {action} | {len(rows)} levels"
        )

    ds.close()


# ================= RUN =================
def run():
    conn = connect_db()
    try:
        for f in sorted(os.listdir(PROF_DIR)):
            if f.endswith(".nc"):
                process_prof_file(conn, os.path.join(PROF_DIR, f))
    finally:
        conn.close()
        print("\n🔒 DB connection closed")


if __name__ == "__main__":
    run()