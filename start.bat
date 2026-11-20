@echo off
setlocal
cd /d "%~dp0"
title Lehmus Chat

if not exist ".venv\Scripts\python.exe" (
    echo First start: setting up Python environment...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python 3.10+ is required. Install it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during installation.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || (pause & exit /b 1)
    copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)

rem Reinstall dependencies if requirements.txt changed after a git pull
fc /b requirements.txt ".venv\installed-requirements.txt" >nul 2>nul || (
    echo Updating dependencies...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt && copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)

".venv\Scripts\python.exe" launcher.py
if errorlevel 1 pause
