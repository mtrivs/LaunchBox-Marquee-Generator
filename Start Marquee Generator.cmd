@echo off
setlocal
cd /d "%~dp0"
set "BUNDLED=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python"
if exist "%BUNDLED%\pythonw.exe" (
  "%BUNDLED%\python.exe" -c "import PIL, tkinter" >nul 2>&1
  if not errorlevel 1 (
    start "" "%BUNDLED%\pythonw.exe" "%~dp0marquee.py"
    exit /b
  )
)
py -3 -c "import PIL, tkinter" >nul 2>&1
if not errorlevel 1 (
  start "" pyw -3 "%~dp0marquee.py"
  exit /b
)
python -c "import PIL, tkinter" >nul 2>&1
if not errorlevel 1 (
  start "" pythonw "%~dp0marquee.py"
  exit /b
)
echo This tool needs Python 3.10 or newer with Pillow and tkinter.
echo If Python is installed, run Install Requirements.cmd once.
echo See README.md for setup instructions.
pause
