"""
MASTER INGESTION RUNNER
ORDER:
1. META
2. TECH
3. RTRAJ
4. PROF
5. SPROF
"""

import logging
import shutil
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("argo_ingestion.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

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
        logger.info("Deleted folder: %s", folder_path)
    else:
        logger.warning("Folder not found (skipped): %s", folder_path)


# ================= MAIN PIPELINE =================

def run_all():
    logger.info("STARTING FULL ARGO INGESTION")

    # ---------- META ----------
    logger.info("STEP 1: META")
    meta_platforms = run_meta_ingestion()
    delete_folder("data_downloads/data_downloads_meta")

    # ---------- TECH ----------
    logger.info("STEP 2: TECH")
    tech_platforms = run_tech_ingestion()
    delete_folder("data_downloads/data_downloads_tech")

    # ---------- RTRAJ ----------
    logger.info("STEP 3: RTRAJ")
    rtraj_platforms = run_rtraj_ingestion()
    delete_folder("data_downloads/data_downloads_rtraj")

    # ---------- PROF ----------
    logger.info("STEP 4: PROF")
    prof_platforms = run_prof_ingestion()
    delete_folder("data_downloads/data_downloads_prof")

    # ---------- SPROF ----------
    logger.info("STEP 5: SPROF")
    sprof_platforms = run_sprof_ingestion()
    delete_folder("data_downloads/data_downloads_sprof")

    # ---------- FLAT TABLE ----------
    # Union all platforms touched in this run
    all_platforms = meta_platforms | tech_platforms | rtraj_platforms | prof_platforms | sprof_platforms
    logger.info("STEP 6: FLAT TABLE (%d platforms affected)", len(all_platforms))
    rebuild_flat_table(platforms=all_platforms if all_platforms else None)

    logger.info("ALL INGESTION COMPLETED")


# ================= ENTRY =================
if __name__ == "__main__":
    run_all()
