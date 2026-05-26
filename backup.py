#!/usr/bin/env python3
"""
EZR Project Tracker — Backup Script
Backs up: SQLite database + full app code
Run manually: python backup.py
Schedule nightly: add to Windows Task Scheduler (see README below)
"""

import os
import sys
import shutil
import zipfile
import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
# Folder where backups will be stored (change this to a network drive or OneDrive path)
BACKUP_DIR = Path(r"C:\Users\ira_b\Documents\project_tracker_backups")

# How many backups to keep (older ones are auto-deleted)
KEEP_LAST_N = 30

# App root (folder containing manage.py)
APP_ROOT = Path(__file__).parent.resolve()
# ─────────────────────────────────────────────────────────────────────────────

def run_backup():
    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M")
    backup_name = f"ezr_backup_{stamp}"
    
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BACKUP_DIR / f"{backup_name}.zip"

    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] Starting backup → {zip_path}")

    # Files/folders to exclude from code backup
    EXCLUDE = {
        '__pycache__', '.git', 'venv', 'env', '.env',
        'staticfiles', 'node_modules', '.pytest_cache',
    }
    # File extensions to exclude
    EXCLUDE_EXT = {'.pyc', '.pyo'}

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:

        # 1. Database (most important)
        db_path = APP_ROOT / 'db.sqlite3'
        if db_path.exists():
            zf.write(db_path, 'db.sqlite3')
            print(f"  ✓ Database: {db_path.stat().st_size / 1024:.1f} KB")
        else:
            print("  ⚠ No db.sqlite3 found — skipping database")

        # 2. Full app code
        for item in APP_ROOT.rglob('*'):
            # Skip excluded folders and extensions
            if any(excl in item.parts for excl in EXCLUDE):
                continue
            if item.suffix in EXCLUDE_EXT:
                continue
            if item.name == 'db.sqlite3':
                continue  # Already added above
            if item.is_file():
                arc_name = 'app_code/' + str(item.relative_to(APP_ROOT))
                zf.write(item, arc_name)

        # 3. Backup manifest
        manifest = f"""EZR Project Tracker Backup
===========================
Date:       {now.strftime('%d %B %Y %H:%M')}
Backup:     {backup_name}.zip

Contents:
  db.sqlite3          — Full SQLite database (all projects, costs, stock, users)
  app_code/           — Complete application source code

HOW TO RESTORE:
===============
1. Extract this zip file
2. Copy db.sqlite3 → C:\\Users\\ira_b\\Documents\\project_tracker\\db.sqlite3
   (this replaces your current database — make a manual backup of the current one first)
3. To also restore the code:
   Copy everything inside app_code/ → C:\\Users\\ira_b\\Documents\\project_tracker\\
4. Restart the server: python manage.py runserver
"""
        zf.writestr('RESTORE_INSTRUCTIONS.txt', manifest)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"  ✓ Backup complete: {zip_path.name} ({size_mb:.2f} MB)")

    # Clean up old backups
    backups = sorted(BACKUP_DIR.glob("ezr_backup_*.zip"))
    if len(backups) > KEEP_LAST_N:
        to_delete = backups[:len(backups) - KEEP_LAST_N]
        for old in to_delete:
            old.unlink()
            print(f"  🗑 Deleted old backup: {old.name}")

    print(f"  Backups kept: {min(len(backups), KEEP_LAST_N)} of last {KEEP_LAST_N}")
    return str(zip_path)


if __name__ == '__main__':
    try:
        result = run_backup()
        print(f"\n✅ Success: {result}")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Backup failed: {e}")
        sys.exit(1)
