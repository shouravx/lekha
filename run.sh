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
    echo "[1/5] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[1/5] Virtual environment already exists, skipping."
fi

source venv/bin/activate

# --- 3. Dependencies -----------------------------------------------------------
# Reinstall when requirements.txt has been edited since the last
# install, rather than skipping forever once the marker exists.
if [ ! -f "venv/.deps_installed" ] || [ requirements.txt -nt "venv/.deps_installed" ]; then
    echo "[2/5] Installing dependencies from requirements.txt..."
    pip install --upgrade pip >/dev/null
    pip install -r requirements.txt
    touch venv/.deps_installed
else
    echo "[2/5] Dependencies already installed, skipping."
fi

# --- 4. Translation models (one-time, needs internet) -------------------------
if [ ! -f "data/.models_downloaded" ]; then
    echo "[3/5] Downloading translation models (first run only, needs internet)..."
    python scripts/download_models.py
    touch data/.models_downloaded
else
    echo "[3/5] Translation models already downloaded, skipping."
fi

# requests warns that chardet's version is outside the range it declares.
# Harmless - requests prefers charset_normalizer, which is present and in
# range - but Streamlit imports requests before any app code runs, so this
# has to be set before Python starts.
# Filtered by module, not by message: PYTHONWARNINGS re.escapes the message
# field, so a pattern there never matches. warnings.filterwarnings in
# config.py does treat it as a regex, which is why that one works.
export PYTHONWARNINGS="ignore:::requests"

# --- 5. Local AI (optional, one-time) ------------------------------------------
# setup_ai.py always exits 0, so a failure here costs the optional polish
# pass and never the launch. Set LEKHA_SKIP_AI=1 to skip it entirely.
echo "[4/5] Checking the local AI..."
python scripts/setup_ai.py

# --- 6. Launch -----------------------------------------------------------------
echo "[5/5] Launching Lekha at http://localhost:8501 ..."
echo "      Press Ctrl+C in this terminal to stop the app."
echo
streamlit run app.py
