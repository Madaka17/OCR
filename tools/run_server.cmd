@echo off
rem Starts the Thai OCR server and appends its output to data\logs\server.log.
rem The log is rotated to server.log.1 when it grows past 10 MB (one old copy kept).
rem Run by the "ThaiOCR Server" scheduled task (see tools\install_server.ps1).
setlocal
cd /d "%~dp0.."
if not exist data\logs mkdir data\logs
set LOG=data\logs\server.log
for %%F in ("%LOG%") do if %%~zF GTR 10485760 move /y "%LOG%" "%LOG%.1" >nul
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m server >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
