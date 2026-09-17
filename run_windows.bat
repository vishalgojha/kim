@echo off
setlocal
cd /d "%~dp0"

rem Kim Windows desktop relay (connect this machine to app.vishalojha.me).
rem First run: set KIM_REMOTE_PIN and a unique KIM_DESKTOP_DEVICE_ID in .env,
rem then install tesseract.exe and run this script (or run.bat desktop).

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - add your KIM_REMOTE_PIN before continuing
)

".venv\Scripts\python.exe" -m aurora desktop
endlocal