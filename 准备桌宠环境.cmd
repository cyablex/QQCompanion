@echo off
cd /d "%~dp0"
runtime\python.exe -X utf8 tools\prepare_vpet.py
pause
