@echo off
rem Double-click to change the Thai OCR passwords (asks for Administrator).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0change_passwords.ps1"
