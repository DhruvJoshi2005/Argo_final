"""
MASTER INGESTION RUNNER
ORDER:
1. META
2. TECH
3. RTRAJ
4. PROF
5. SPROF
"""

import shutil
import os

# ================= IMPORT ALL RUNNERS =================

# META
from ingestion_logic.meta_ingestion import run_meta_ingestion

# TECH
from ingestion_logic.tech_ingestion import run_tech_ingestion

# RTRAJ
from ingestion_logic.rtraj_ingestion import run_rtraj_ingestion

# PROF
from ingestion_logic.prof_ingestion import run as run_prof_ingestion

# SPROF
from ingestion_logic.sprof_ingestion import run as run_sprof_ingestion

# FLAT TABLE
from ingestion_logic.flat_table_ingestion import rebuild_flat_table


# ================= FOLDER CLEANUP UTILITY =================

def delete_folder(folder_path: str):
    """
    Safely delete a folder after successful ingestion
    """
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
        print(f"🗑️ Deleted folder: {folder_path}")
    else:
        print(f"⚠️ Folder not found (skipped): {folder_path}")


# ================= MAIN PIPELINE =================

def run_all():
    print("\n================= 🚀 STARTING FULL ARGO INGESTION =================")

    # ---------- META ----------
    print("\n🔹 STEP 1: META")
    run_meta_ingestion()
    delete_folder("data_downloads/data_downloads_meta")

    # ---------- TECH ----------
    print("\n🔹 STEP 2: TECH")
    run_tech_ingestion()
    delete_folder("data_downloads/data_downloads_tech")

    # ---------- RTRAJ ----------
    print("\n🔹 STEP 3: RTRAJ")
    run_rtraj_ingestion()
    delete_folder("data_downloads/data_downloads_rtraj")

    # ---------- PROF ----------
    print("\n🔹 STEP 4: PROF")
    run_prof_ingestion()
    delete_folder("data_downloads/data_downloads_prof")

    # ---------- SPROF ----------
    print("\n🔹 STEP 5: SPROF")
    run_sprof_ingestion()
    delete_folder("data_downloads/data_downloads_sprof")

    # ---------- FLAT TABLE ----------
    print("\n🔹 STEP 6: FLAT TABLE")
    rebuild_flat_table()

    print("\n================= ✅ ALL INGESTION COMPLETED =================")


# ================= ENTRY =================
if __name__ == "__main__":
    run_all()
