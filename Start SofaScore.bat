@echo off
REM Double-click to start SofaScore Scraper (Windows)
cd /d "%~dp0"
title SofaScore Scraper
where py >nul 2>nul && (
  py -3 scripts\start_web.py
  goto :end
)
where python >nul 2>nul && (
  python scripts\start_web.py
  goto :end
)
REM Language: an explicit setting (SOFASCORE_DISPLAY__LANGUAGE in the environment or in .env) wins, then the
REM Windows display language, then English: the same rule as the app (sofascore_scraper\language.py). Only this
REM message needs it; everything else is printed by scripts\start_web.py. The Turkish text has no
REM Turkish letters on purpose: this file stays plain ASCII, so it reads the same in every code page.
set "UI_LANG=en"
for /f "usebackq delims=" %%L in (`powershell -NoProfile -Command "(Get-UICulture).TwoLetterISOLanguageName" 2^>nul`) do set "UI_LANG=%%L"
findstr /b /r /i /c:"SOFASCORE_DISPLAY__LANGUAGE *= *tr" .env >nul 2>nul && set "UI_LANG=tr"
findstr /b /r /i /c:"SOFASCORE_DISPLAY__LANGUAGE *= *en" .env >nul 2>nul && set "UI_LANG=en"
if /i "%SOFASCORE_DISPLAY__LANGUAGE%"=="tr" set "UI_LANG=tr"
if /i "%SOFASCORE_DISPLAY__LANGUAGE%"=="en" set "UI_LANG=en"
if /i "%UI_LANG%"=="tr" goto :no_python_tr
echo Python not found. Install Python 3 from https://www.python.org/downloads/
echo Then re-run this file.
pause
exit /b 1
:no_python_tr
echo Python bulunamadi. Python 3'u https://www.python.org/downloads/ adresinden kurun,
echo sonra bu dosyayi yeniden calistirin.
pause
exit /b 1
:end
if errorlevel 1 pause
