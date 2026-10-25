@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 "%~dp0tools\bootstrap.py" %*
if errorlevel 1 echo 请检查提示。首次准备需要 Python 3.12、Node.js 和联网；私人迁移可使用恢复工具包。
pause
