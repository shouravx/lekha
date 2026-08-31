"""scripts/setup_ai.py — installs the local AI, without a browser.

The Settings page can do this interactively. This is the same thing for
the launchers, so a machine nobody is sitting in front of — the tunnel
case — comes up fully provisioned.

Three properties matter here, because this runs unattended:

  idempotent  It checks what is already there and exits immediately when
              there is nothing to do, so it costs a second on every
              subsequent launch rather than re-downloading anything.

  non-fatal   A failure here must never stop the app from starting. The
              polish pass is optional; translation is not. Every failure
              path exits 0 with an explanation, so the launcher carries on
              to the app.

  honest      It says what it is about to download, and how big, before it
              starts.

Usage:
    python scripts/setup_ai.py            # install if needed
    python scripts/setup_ai.py --check    # report status, change nothing
    python scripts/setup_ai.py --model X  # a model other than the default

Set LEKHA_SKIP_AI=1 to make it do nothing at all.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


class _Progress:
    """One rewriting console line, so a long download does not print a
    thousand of them into a launcher window."""

    def __init__(self) -> None:
        self._last = 0.0
        self._width = 0

    def __call__(self, message: str, fraction: float = -1.0) -> None:
        now = time.monotonic()
        done = fraction >= 1.0
        # Throttle: the pull stream emits events far faster than anyone
        # can read them.
        if not done and now - self._last < 0.4:
            return
        self._last = now

        if 0.0 <= fraction <= 1.0:
            filled = int(fraction * 24)
            bar = "#" * filled + "." * (24 - filled)
            line = f"      [{bar}] {fraction * 100:5.1f}%  {message}"
        else:
            line = f"      {message}"

        line = line[:110]
        sys.stdout.write("\r" + line.ljust(self._width))
        self._width = max(self._width, len(line))
        sys.stdout.flush()

    def done(self, message: str = "") -> None:
        sys.stdout.write("\r" + " " * self._width + "\r")
        if message:
            print(f"      {message}")
        sys.stdout.flush()
        self._width = 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install and prepare Lekha's local AI.")
    parser.add_argument("--check", action="store_true",
                        help="Report what is installed and exit without changing anything.")
    parser.add_argument("--model", default=config.REFINE_MODEL,
                        help=f"Model to make available (default: {config.REFINE_MODEL}).")
    parser.add_argument("--yes", action="store_true",
                        help="Accepted for symmetry; this script never prompts.")
    args = parser.parse_args()

    if os.environ.get("LEKHA_SKIP_AI", "").strip() not in ("", "0", "false", "False"):
        print("      LEKHA_SKIP_AI is set - skipping local AI setup.")
        return 0

    try:
        from services.ai_runtime import ai_runtime
    except Exception as exc:  # noqa: BLE001
        print(f"      Could not load the AI runtime helper: {exc}")
        return 0

    base_url = config.OLLAMA_BASE_URL
    model = args.model
    status = ai_runtime.status(base_url)

    if args.check:
        print(f"      runtime: {status.binary or 'not installed'}"
              f"{' (managed by Lekha)' if status.managed else ''}")
        print(f"      server : {'running' if status.server_running else 'stopped'}")
        print(f"      model  : {model} {'ready' if status.has_model(model) else 'not pulled'}")
        return 0

    # Nothing to do is the common case on every launch after the first.
    if status.installed and status.has_model(model):
        print("      Local AI already set up, skipping.")
        return 0

    progress = _Progress()

    try:
        if not status.installed:
            try:
                asset, size = ai_runtime.download_size()
            except Exception as exc:  # noqa: BLE001
                print(f"      Could not reach the Ollama release feed ({exc}).")
                print("      Skipping local AI setup; translation is unaffected.")
                return 0

            print(f"      Installing the local AI runtime ({size / 1048576:.0f} MB "
                  f"download, plus ~2 GB for {model}).")
            print("      This happens once. Set LEKHA_SKIP_AI=1 to skip it in future.")
            ai_runtime.install(progress)
            progress.done("Runtime installed.")
        else:
            print(f"      Runtime found at {status.binary}")

        if not ai_runtime.start_server(base_url):
            print("      The AI server did not start; skipping the model pull.")
            print("      Translation is unaffected - the polish pass will be skipped.")
            return 0

        status = ai_runtime.status(base_url)
        if status.has_model(model):
            print(f"      Model {model} already available.")
        else:
            print(f"      Pulling {model} ...")
            if ai_runtime.pull_model(model, base_url, progress):
                progress.done(f"Model {model} is ready.")
            else:
                progress.done(f"Could not pull {model}; the polish pass will be skipped.")

    except KeyboardInterrupt:
        progress.done("Interrupted.")
        return 0
    except Exception as exc:  # noqa: BLE001
        progress.done(f"Local AI setup did not complete: {exc}")
        print("      Translation is unaffected; the polish pass will be skipped.")
        return 0
    finally:
        # The app starts the server itself when a job needs it, so the
        # one this script started is not left running afterwards.
        try:
            ai_runtime.stop_server()
        except Exception:  # noqa: BLE001
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
