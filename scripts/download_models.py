"""scripts/download_models.py — one-time setup script.

Lekha's main application is 100% offline at runtime, but Argos
Translate's language models still need to be downloaded *once* before
first use. Run this script with an internet connection; afterwards the
app never needs network access again.

Usage:
    python scripts/download_models.py
    python scripts/download_models.py --pairs en-bn bn-en
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def parse_pairs(raw_pairs: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for raw in raw_pairs:
        if "-" not in raw:
            print(f"Skipping invalid pair '{raw}' (expected format: en-bn)")
            continue
        source, target = raw.split("-", 1)
        pairs.append((source.strip(), target.strip()))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Argos Translate language models.")
    parser.add_argument(
        "--pairs", nargs="*", default=None,
        help="Language pairs to install, e.g. --pairs en-bn bn-en. "
        "Defaults to config.SUPPORTED_LANGUAGE_PAIRS.",
    )
    args = parser.parse_args()

    pairs = parse_pairs(args.pairs) if args.pairs else config.SUPPORTED_LANGUAGE_PAIRS

    print(f"{config.APP_NAME} — model downloader")
    print(f"Installing {len(pairs)} language pair(s): {pairs}")
    print("This requires an internet connection and only needs to be run once.\n")

    import argostranslate.package as argos_package

    print("Updating Argos Translate package index...")
    argos_package.update_package_index()
    available_packages = argos_package.get_available_packages()

    installed_already = {
        (p.from_code, p.to_code) for p in argos_package.get_installed_packages()
    }

    for source, target in pairs:
        if (source, target) in installed_already:
            print(f"✓ {source} -> {target} already installed, skipping.")
            continue

        match = next(
            (p for p in available_packages if p.from_code == source and p.to_code == target),
            None,
        )
        if match is None:
            print(f"✗ No Argos Translate package found for '{source}' -> '{target}'. Skipping.")
            continue

        print(f"Downloading {source} -> {target}...")
        download_path = match.download()
        argos_package.install_from_path(download_path)
        print(f"✓ Installed {source} -> {target}.")

    print("\nDone. You can now run the app fully offline with:")
    print("    streamlit run app.py")


if __name__ == "__main__":
    main()
