#!/usr/bin/env python3
"""
Migration script: Rename match_details league folders from 'League_Name' to 'ID_League_Name' format.

Scans match_details/ for folders without an integer ID prefix, looks up the league ID
from config/leagues.txt, and renames them.

Usage:
    python scripts/migrate_match_details.py [--dry-run] [--data-dir DATA_DIR]
"""

import os
import sys
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config_manager import ConfigManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MigrateMatchDetails")


def main():
    parser = argparse.ArgumentParser(description="Migrate match_details folder names to include league ID prefix")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be renamed without actually renaming")
    parser.add_argument("--data-dir", default=None, help="Data directory (default: from config)")
    args = parser.parse_args()

    config_manager = ConfigManager()
    data_dir = args.data_dir or config_manager.get_data_dir()
    match_details_dir = os.path.join(data_dir, "match_details")

    if not os.path.exists(match_details_dir):
        logger.info(f"No match_details directory found at {match_details_dir}")
        return

    leagues = config_manager.get_leagues()
    name_to_id = {}
    for lid, lname in leagues.items():
        safe_name = lname.replace(' ', '_').replace('/', '_')
        name_to_id[safe_name.lower()] = (lid, safe_name)

    renamed = 0
    skipped = 0
    for folder in os.listdir(match_details_dir):
        folder_path = os.path.join(match_details_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        if folder == "processed":
            continue

        try:
            int(folder.split("_")[0])
            logger.debug(f"Already prefixed: {folder}")
            continue
        except (ValueError, IndexError):
            pass

        lookup_key = folder.lower()
        if lookup_key in name_to_id:
            lid, safe_name = name_to_id[lookup_key]
            new_name = f"{lid}_{safe_name}"
            new_path = os.path.join(match_details_dir, new_name)
            if os.path.exists(new_path):
                logger.warning(f"SKIP: {folder} -> {new_name} (target already exists)")
                skipped += 1
                continue
            if args.dry_run:
                logger.info(f"DRY RUN: {folder} -> {new_name}")
            else:
                os.rename(folder_path, new_path)
                logger.info(f"RENAMED: {folder} -> {new_name}")
            renamed += 1
        else:
            logger.warning(f"SKIP: {folder} (no matching league found in config)")
            skipped += 1

    logger.info(f"Done. Renamed: {renamed}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
