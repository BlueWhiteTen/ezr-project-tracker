EZR PROJECT TRACKER — BACKUP GUIDE
====================================

WHAT GETS BACKED UP
-------------------
Each backup is a single .zip file containing:
  • db.sqlite3          — the entire database (projects, costs, stock, users, everything)
  • app_code/           — the full application code
  • RESTORE_INSTRUCTIONS.txt — restore steps inside every zip

Backups are saved to: C:\Users\ira_b\Documents\project_tracker_backups\
Named like:           ezr_backup_2026-05-25_20-00.zip
The last 30 days are kept automatically.


STEP 1 — SET UP NIGHTLY BACKUPS (do this once)
-----------------------------------------------
1. Right-click  schedule_backup.bat
2. Select "Run as administrator"
3. Done — backups now run every evening at 8:00 PM automatically

To verify: open Windows Task Scheduler → look for "EZR_ProjectTracker_Backup"


STEP 2 — RUN A MANUAL BACKUP ANYTIME
--------------------------------------
Open a terminal in the project folder and run:

    python backup.py


HOW TO RESTORE
--------------
Everything you need is inside the zip file.

DATABASE ONLY (most common — e.g. data got corrupted):
  1. Open the backup zip
  2. Copy db.sqlite3 to:  C:\Users\ira_b\Documents\project_tracker\
  3. Restart the server:  python manage.py runserver

FULL RESTORE (code + database — e.g. after moving to a new PC):
  1. Install Python and run:  pip install -r requirements.txt
  2. Extract the zip
  3. Copy db.sqlite3 → C:\Users\ira_b\Documents\project_tracker\
  4. Copy app_code\ contents → C:\Users\ira_b\Documents\project_tracker\
  5. Run:  python manage.py migrate
  6. Run:  python manage.py runserver
  7. Go to http://127.0.0.1:8000

MOVING TO A NEW PC
------------------
Just copy the most recent backup zip to the new machine and follow
the "Full Restore" steps above. Your logins, all project data,
stock, costings and documents will all be there.


BACKUP LOCATION — RECOMMENDATION
----------------------------------
Consider changing BACKUP_DIR in backup.py to point to:
  • A OneDrive or Dropbox folder (auto-syncs to cloud)
  • A network drive
  • A USB drive

This protects you if the PC itself fails. Edit backup.py line 16:
    BACKUP_DIR = Path(r"C:\Users\ira_b\OneDrive\EZR_Backups")

