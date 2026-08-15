@echo off
REM Double-click to start SofaScore Scraper (Windows)
cd /d "%~dp0.."
title SofaScore Scraper
where py >nul 2>nul && (
  py -3 scripts\start_web.py
  goto :end
)
where python >nul 2>nul && (
  python scripts\start_web.py
  goto :end
)
echo Python not found. Install Python 3 from https://www.python.org/downloads/
echo Then re-run this file.
pause
exit /b 1
:end
if errorlevel 1 pause
