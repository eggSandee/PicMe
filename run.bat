@echo off
REM PicMe — Windows launcher
REM Usage: run.bat   (runs on port 8000)

set REPO_DIR=%~dp0
set PYTHON=%REPO_DIR%.venv\Scripts\python.exe

if not exist "%PYTHON%" (
    echo ERROR: virtualenv not found at .venv\
    echo Run the setup steps in README.md -- "Running on Windows" first.
    exit /b 1
)

cd /d "%REPO_DIR%"
"%PYTHON%" backend\main.py
