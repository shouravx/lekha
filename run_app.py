"""run_app.py — PyInstaller entrypoint.

`streamlit run app.py` relies on the Streamlit CLI, which doesn't exist
as an importable entrypoint once bundled into a frozen executable. This
script boots the same app programmatically via `streamlit.web.cli`, so
PyInstaller has a single, normal Python entrypoint to compile.

Usage (development):
    python run_app.py

Usage (after PyInstaller build):
    dist/Lekha/Lekha.exe
"""

from __future__ import annotations

import os
import sys


def _app_path() -> str:
    """Resolves app.py's path whether running from source or from a
    PyInstaller-frozen bundle (where files are extracted to sys._MEIPASS
    or live alongside the .exe under --onedir)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "app.py")


def main() -> None:
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        _app_path(),
        "--global.developmentMode=false",
        "--server.headless=false",
        "--browser.gatherUsageStats=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
