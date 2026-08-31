#!/usr/bin/env bash
# ============================================================
# run.sh — one-click setup + launch for Lekha (macOS/Linux)
#
# What it does, in order:
#   1. Creates a venv/ virtual environment (only if missing)
#   2. Installs requirements.txt (only if not already installed)
#   3. Downloads Argos Translate models (only if not already
#      downloaded — needs internet the FIRST time only)
#   4. Launches the app at http://localhost:8501
#
# Safe to run every time — every step is skipped if already
# done, so re-running this is just "start the app."
#
# Usage:
#   chmod +x run.sh   (first time only)
#   ./run.sh
# ============================================================

set -e
cd "$(dirname "$0")"

echo
echo "============================================"
echo "  Lekha — setup and launch"
echo "============================================"
echo

# --- 1. Python check --------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 was not found on PATH."
    echo "        Install Python 3.10+ and re-run this script."
    exit 1
fi

# --- 2. Virtual environment --------------------------------------------------
if [ ! -f "venv/bin/activate" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[1/4] Virtual environment already exists, skipping."
fi

source venv/bin/activate

# --- 3. Dependencies -----------------------------------------------------------
# Reinstall when requirements.txt has been edited since the last
# install, rather than skipping forever once the marker exists.
if [ ! -f "venv/.deps_installed" ] || [ requirements.txt -nt "venv/.deps_installed" ]; then
    echo "[2/4] Installing dependencies from requirements.txt..."
    pip install --upgrade pip >/dev/null
    pip install -r requirements.txt
    touch venv/.deps_installed
else
    echo "[2/4] Dependencies already installed, skipping."
fi

# --- 4. Translation models (one-time, needs internet) -------------------------
if [ ! -f "data/.models_downloaded" ]; then
    echo "[3/4] Downloading translation models (first run only, needs internet)..."
    python scripts/download_models.py
    touch data/.models_downloaded
else
    echo "[3/4] Translation models already downloaded, skipping."
fi

# --- 5. Launch -----------------------------------------------------------------
echo "[4/4] Launching Lekha at http://localhost:8501 ..."
echo "      Press Ctrl+C in this terminal to stop the app."
echo
streamlit run app.py
