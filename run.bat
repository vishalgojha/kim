@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - put your ELEVENLABS_API_KEY in it
)

".venv\Scripts\python.exe" -m aurora %*
endlocal