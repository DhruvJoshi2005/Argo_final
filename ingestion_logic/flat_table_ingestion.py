import psycopg2

DB_PARAMS = {
    "host": "localhost",
    "port": 5432,
    "database": "argo_final",
    "user": "postgres",
    "password": "Dhruv@2005"
}


def rebuild_flat_table():
    print("\n🔹 Rebuilding float_measurements_flat...")

    conn = psycopg2.connect(**DB_PARAMS)
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE float_measurements_flat RESTART IDENTITY")

            cur.execute("""
                INSERT INTO float_measurements_flat
                    (platform_number, cycle_number, juld, latitude, longitude,
                     pressure, temperature, salinity, profile_type, direction)
                SELECT
                    fc.platform_number,
                    fc.cycle_number,
                    fc.juld,
                    fc.latitude,
                    fc.longitude,
                    fpl.pres   AS pressure,
                    fpl.temp   AS temperature,
                    fpl.psal   AS salinity,
                    fp.profile_type,
                    fp.direction
                FROM float_profile_levels fpl
                JOIN float_profiles  fp ON fpl.profile_id = fp.id
                JOIN float_cycles    fc ON fp.cycle_id    = fc.id
                WHERE fpl.pres IS NOT NULL
            """)

            cur.execute("SELECT COUNT(*) FROM float_measurements_flat")
            count = cur.fetchone()[0]
            print(f"✅ float_measurements_flat populated: {count:,} rows")

    finally:
        conn.close()
