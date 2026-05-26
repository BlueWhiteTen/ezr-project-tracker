@echo off
:: EZR Project Tracker — Schedule Nightly Backup via Windows Task Scheduler
:: Run this .bat file ONCE as Administrator to register the nightly task
:: After that, backups run automatically every evening at 8pm

set PYTHON=python
set SCRIPT=C:\Users\ira_b\Documents\project_tracker\backup.py
set TASK_NAME=EZR_ProjectTracker_Backup
set RUN_TIME=20:00

echo Registering nightly backup task...

:: Also schedule daily email reminders at 8am
schtasks /create /tn "EZR_EmailReminders" /tr "cmd /c cd /d C:\Users\ira_b\Documents\project_tracker && %PYTHON% manage.py send_reminders" /sc daily /st 08:00 /f /rl HIGHEST /sd today

echo Registering email reminder task...

schtasks /create /tn "%TASK_NAME%" /tr "%PYTHON% %SCRIPT%" /sc daily /st %RUN_TIME% /f /rl HIGHEST

if %errorlevel% == 0 (
    echo.
    echo SUCCESS! Backup task registered.
    echo It will run every evening at 8:00 PM automatically.
    echo.
    echo To check it worked: open Task Scheduler, look for "%TASK_NAME%"
    echo To run it manually now: python backup.py
    echo Backups saved to: C:\Users\ira_b\Documents\project_tracker_backups\
) else (
    echo.
    echo ERROR: Failed to register task. Try running as Administrator.
    echo Right-click schedule_backup.bat and select "Run as administrator"
)
pause
