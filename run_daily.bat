@echo off
cd /d "%~dp0"

if not exist output mkdir output

".venv\Scripts\python.exe" run_daily.py >> "output\daily_run.log" 2>&1