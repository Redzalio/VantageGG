@echo off
REM One-click launch for the CS2 Demo Player
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip demoparser2 flask
)
if not exist "static\maps\maps.json" (
  echo Fetching radar images...
  ".venv\Scripts\python.exe" fetch_radars.py
)
REM free port 8770 from any stale/zombie server before starting
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8770 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1
REM open on localhost (matches PUBLIC_BASE_URL in .env so Steam login + its cookie line up)
start "" http://localhost:8770
".venv\Scripts\python.exe" app.py
pause
