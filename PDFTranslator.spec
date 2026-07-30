# PDFTranslator.spec — PyInstaller build spec for PDF Translator.
#
# Build with:
#     pyinstaller PDFTranslator.spec
#
# Output lands in dist/PDFTranslator/ (onedir build — recommended for
# Streamlit apps; see README.md for why --onefile is not used).

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)  # noqa: F821 (SPECPATH is injected by PyInstaller)

# Streamlit ships its own static/frontend assets and a metadata-driven
# component system that PyInstaller's static analysis can't always see —
# collect_all() pulls in everything reliably at the cost of a larger build.
from PyInstaller.utils.hooks import collect_all  # noqa: E402

datas = []
binaries = []
hiddenimports = []

for pkg in ("streamlit", "argostranslate", "altair", "pandas"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        pass  # optional packages (e.g. pandas/altair may not be installed)

# This project's own source — bundled as data so app.py's relative
# imports (config, core.*, services.*, models.*, ui.*) resolve at
# runtime exactly as they do when run from source.
project_datas = [
    (str(ROOT / "app.py"), "."),
    (str(ROOT / "config.py"), "."),
    (str(ROOT / "core"), "core"),
    (str(ROOT / "services"), "services"),
    (str(ROOT / "models"), "models"),
    (str(ROOT / "ui"), "ui"),
    (str(ROOT / "assets"), "assets"),
]
datas += project_datas

hiddenimports += [
    "streamlit.runtime.scriptrunner.magic_funcs",
    "argostranslate.translate",
    "argostranslate.package",
    "docx",
    "reportlab.pdfbase._fontdata",
    "fitz",
]

a = Analysis(
    ["run_app.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["paddleocr", "paddlepaddle"],  # see note below
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PDFTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # set True temporarily if you need to see startup errors
    icon=None,      # point this at an .ico file for a custom app icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PDFTranslator",
)

# NOTE on OCR (PaddleOCR / PaddlePaddle):
# These are excluded from the bundle by default because they add several
# hundred MB and most users won't enable OCR. If you want OCR to work in
# the packaged .exe out of the box, remove them from `excludes` above and
# re-run PyInstaller — expect a significantly larger dist/ folder and a
# longer build time. Otherwise, end users who need OCR can
# `pip install paddleocr paddlepaddle` into the same Python environment
# this was built from and rebuild.
