@echo off
rem Starts CSAFI Reels Library Bot on Windows without Docker.
rem Put a shortcut to this file in Task Scheduler ("At log on") to start it on boot.
cd /d "%~dp0"

if not exist .venv (
  echo Creating virtual environment...
  py -3.12 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
)
if not exist .env copy .env.example .env >nul

rem Start Ollama if it is not already running.
tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul || start "" /B ollama serve

.venv\Scripts\python -m csafi_bot serve
