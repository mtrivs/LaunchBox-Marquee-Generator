@echo off
cd /d "%~dp0"
py -3 -m pip install --user -r requirements.txt
if errorlevel 1 python -m pip install --user -r requirements.txt
pause
