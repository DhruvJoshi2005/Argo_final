import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "argo_final"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD")
}


def rebuild_flat_table():
    print("\n🔹 Rebuilding float_measurements_flat...")

    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            # Add new columns if not already present (safe to run repeatedly)
            new_columns = [
                ("doxy",             "FLOAT"),
                ("chla",             "FLOAT"),
                ("bbp700",           "FLOAT"),
                ("ph_in_situ_total", "FLOAT"),
                ("pi_name",          "VARCHAR"),
                ("project_name",     "VARCHAR"),
                ("institution",      "VARCHAR"),
            ]
            for col_name, col_type in new_columns:
                cur.execute(f"""
                    ALTER TABLE float_measurements_flat
                    ADD COLUMN IF NOT EXISTS {col_name} {col_type}
                """)

            cur.execute("TRUNCATE TABLE float_measurements_flat RESTART IDENTITY")

            cur.execute("""
                INSERT INTO float_measurements_flat
                    (platform_number, cycle_number, juld, latitude, longitude,
                     pressure, temperature, salinity, profile_type, direction,
                     doxy, chla, bbp700, ph_in_situ_total,
                     pi_name, project_name, institution)
                SELECT
                    fc.platform_number,
                    fc.cycle_number,
                    fc.juld,
                    fc.latitude,
                    fc.longitude,
                    fpl.pres                              AS pressure,
                    fpl.temp                              AS temperature,
                    fpl.psal                              AS salinity,
                    fp.profile_type,
                    fp.direction,
                    (fpl.qc->>'doxy')::FLOAT              AS doxy,
                    (fpl.qc->>'chla')::FLOAT              AS chla,
                    (fpl.qc->>'bbp700')::FLOAT            AS bbp700,
                    (fpl.qc->>'ph_in_situ_total')::FLOAT  AS ph_in_situ_total,
                    fm.pi_name,
                    fm.project_name,
                    fm.institution
                FROM float_profile_levels fpl
                JOIN float_profiles  fp ON fpl.profile_id = fp.id
                JOIN float_cycles    fc ON fp.cycle_id    = fc.id
                LEFT JOIN float_meta fm ON fc.platform_number = fm.platform_number
                WHERE fpl.pres IS NOT NULL
            """)

            cur.execute("SELECT COUNT(*) FROM float_measurements_flat")
            count = cur.fetchone()[0]
            print(f"✅ float_measurements_flat populated: {count:,} rows")

    finally:
        conn.close()
