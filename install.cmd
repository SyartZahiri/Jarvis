@echo off
rem Doppelklick-Installer: startet install.ps1 nur fuer diesen Aufruf ohne Skript-Sperre.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
