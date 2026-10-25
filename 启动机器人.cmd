@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0runtime\python.exe" "%~dp0app\services.py" start bot watchdog
pause
