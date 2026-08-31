@echo off
REM ============================================================
REM run.bat — one-click setup + launch for Lekha
REM
REM What it does, in order:
REM   1. Creates a venv\ virtual environment (only if missing)
REM   2. Installs requirements.txt (only if not already installed)
REM   3. Downloads Argos Translate models (only if not already
REM      downloaded — needs internet the FIRST time only)
REM   4. Launches the app at http://localhost:8501
REM
REM Safe to double-click every time — every step is skipped if
REM already done, so re-running this is just "start the app."
REM ============================================================

setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo   Lekha — setup and launch
echo ============================================
echo.

REM --- 1. Python check ----------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.10+ from https://python.org and re-run this script.
    pause
    exit /b 1
)

REM --- 2. Virtual environment ----------------------------------------------
if not exist "venv\Scripts\activate.bat" (
    echo [1/5] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Virtual environment already exists, skipping.
)

call venv\Scripts\activate.bat

REM --- 3. Dependencies -------------------------------------------------------
REM The marker records a hash of requirements.txt rather than just
REM "something was installed once", so editing that file (for example to
REM add the optional hybrid-pipeline packages) triggers a reinstall
REM instead of being silently skipped.
set "REQ_HASH="
for /f "skip=1 delims=" %%h in ('certutil -hashfile requirements.txt MD5') do (
    if not defined REQ_HASH set "REQ_HASH=%%h"
)
REM If certutil is unavailable the hash is empty; force a reinstall rather
REM than matching an equally-empty marker and skipping silently.
if not defined REQ_HASH set "REQ_HASH=unknown-%RANDOM%"

set "OLD_HASH="
if exist "venv\.deps_installed" set /p OLD_HASH=<venv\.deps_installed

if not "%REQ_HASH%"=="%OLD_HASH%" (
    echo [2/5] Installing dependencies from requirements.txt...
    pip install --upgrade pip >nul
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. See the output above.
        pause
        exit /b 1
    )
    > "venv\.deps_installed" echo %REQ_HASH%
) else (
    echo [2/5] Dependencies already installed, skipping.
)

REM --- 4. Translation models (one-time, needs internet) ----------------------
if not exist "data\.models_downloaded" (
    echo [3/5] Downloading translation models ^(first run only, needs internet^)...
    python scripts\download_models.py
    if errorlevel 1 (
        echo [ERROR] Model download failed. Check your internet connection and re-run.
        pause
        exit /b 1
    )
    echo done > data\.models_downloaded
) else (
    echo [3/5] Translation models already downloaded, skipping.
)

REM --- 5. Local AI (optional, one-time) -------------------------------------
REM Never fatal: setup_ai.py always exits 0, so a failure here can only
REM cost the optional polish pass, never the launch. Set LEKHA_SKIP_AI=1
REM to skip it entirely.
echo [4/5] Checking the local AI...
python scripts\setup_ai.py

REM --- 6. Launch ---------------------------------------------------------
echo [5/5] Launching Lekha at http://localhost:8501 ...
echo       Press Ctrl+C in this window to stop the app.
echo.
streamlit run app.py

endlocal
