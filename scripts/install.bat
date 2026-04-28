@echo off
REM SofaScore Scraper — Windows CMD: scripts\install.bat  veya  install.bat -RepoUrl https://github.com/...
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
