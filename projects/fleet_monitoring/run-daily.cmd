@echo off
REM Daily Fleet Monitoring run. Pointed at by Windows Task Scheduler ("FleetMonitoring").
REM Logs each run's stdout/stderr to data/run-daily.log (gitignored).
cd /d D:\nkp-ops
python projects\fleet_monitoring\run.py --no-probes >> projects\fleet_monitoring\data\run-daily.log 2>&1
