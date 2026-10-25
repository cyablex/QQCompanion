@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist "%~dp0..\QQCompanionFrontendTools\python-env\Scripts\pythonw.exe" (
  echo Desktop tool environment not found beside this installation.
  pause
  exit /b 1
)
start "" "%~dp0..\QQCompanionFrontendTools\python-env\Scripts\pythonw.exe" "%~dp0app\desktop.py"
