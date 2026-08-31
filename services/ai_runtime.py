"""services/ai_runtime.py — installs and runs the local AI by itself.

The polish pass needs Ollama. Until now the app could only tell the user
to go and install it, which is fine on a desktop and useless on a machine
reached through a tunnel, where there may be nobody sitting in front of
it. This module makes Lekha responsible for the whole thing: fetch the
runtime, verify it, start it, pull the model, and shut it down again.

What it will and will not do
----------------------------
Everything here is user-initiated. Nothing downloads on import, on app
start, or as a side effect of translating; a job whose refiner is missing
still degrades to plain translation rather than quietly pulling three
gigabytes. The size is stated before anything is fetched.

The binary is downloaded only from the official ollama/ollama GitHub
release, over HTTPS, and its SHA-256 is checked against the release's own
sha256sum.txt before a single byte is executed. Downloading an executable
and running it without verifying it would be the wrong thing to do on
someone else's machine, however convenient.

The server is a child process of the app. It is started on demand and
stopped with the app, so nothing is left listening after Lekha exits.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import platform
import shutil
import subprocess
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import config
from services.logger_service import get_logger

logger = get_logger("ai_runtime")

RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"

ProgressCallback = Callable[[str, float], None]  # (message, 0.0-1.0 or -1)


@dataclass
class RuntimeStatus:
    binary: Optional[Path]
    managed: bool           # True when Lekha installed it, rather than the system
    server_running: bool
    models: list[str]

    @property
    def installed(self) -> bool:
        return self.binary is not None

    def has_model(self, name: str) -> bool:
        # Ollama reports "qwen2.5:3b"; a name given without a tag should
        # still match the default-tagged install.
        wanted = name if ":" in name else f"{name}:latest"
        return any(m == wanted or m == name or m.startswith(f"{name}:") for m in self.models)


def _asset_name() -> Optional[str]:
    """The release asset for this machine.

    Names are resolved against the live release rather than hardcoded:
    the Linux asset was renamed from .tgz to .tar.zst, and a URL frozen
    into the source would simply 404 one day.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")

    if system == "windows":
        return "ollama-windows-arm64.zip" if arm else "ollama-windows-amd64.zip"
    if system == "darwin":
        return "ollama-darwin.tgz"
    if system == "linux":
        return "ollama-linux-arm64.tar.zst" if arm else "ollama-linux-amd64.tar.zst"
    return None


class AIRuntime:
    """Owns the managed Ollama install and its server process."""

    _instance: Optional["AIRuntime"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.root = config.RUNTIME_DIR / "ollama"
        self._process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "AIRuntime":
        with cls._lock:
            if cls._instance is None:
                cls._instance = AIRuntime()
            return cls._instance

    # -- discovery ---------------------------------------------------------
    def _managed_binary(self) -> Optional[Path]:
        name = "ollama.exe" if os.name == "nt" else "ollama"
        for candidate in (self.root / name, self.root / "bin" / name):
            if candidate.exists():
                return candidate
        # The archives nest their layout differently per platform; a
        # bounded search is more robust than encoding each one.
        if self.root.exists():
            for found in self.root.rglob(name):
                if found.is_file():
                    return found
        return None

    def resolve_binary(self) -> tuple[Optional[Path], bool]:
        """(path, managed). An existing system install always wins — the
        user's own Ollama, with whatever models they have already pulled,
        is better than a second copy."""
        system = shutil.which("ollama")
        if system:
            return Path(system), False
        return self._managed_binary(), True

    def status(self, base_url: str = "") -> RuntimeStatus:
        binary, managed = self.resolve_binary()
        models = self._list_models(base_url or config.OLLAMA_BASE_URL)
        return RuntimeStatus(
            binary=binary,
            managed=managed and binary is not None,
            server_running=models is not None,
            models=models or [],
        )

    def _list_models(self, base_url: str) -> Optional[list[str]]:
        """Model names, or None when the server is not answering."""
        try:
            import requests

            response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=4)
            response.raise_for_status()
            return [m.get("name", "") for m in response.json().get("models", [])]
        except Exception:  # noqa: BLE001
            return None

    # -- install -------------------------------------------------------------
    def download_size(self) -> tuple[str, int]:
        """(asset name, bytes) for this platform, straight from the release."""
        import requests

        asset = _asset_name()
        if asset is None:
            raise RuntimeError(f"No Ollama build for {platform.system()} {platform.machine()}.")

        release = requests.get(RELEASE_API, timeout=30).json()
        for item in release.get("assets", []):
            if item.get("name") == asset:
                return asset, int(item.get("size", 0))
        raise RuntimeError(f"The current Ollama release has no asset named {asset}.")

    def install(self, progress: Optional[ProgressCallback] = None) -> Path:
        """Downloads, verifies and extracts the runtime. Returns the binary."""
        import requests

        def report(message: str, fraction: float = -1.0) -> None:
            logger.info("%s", message)
            if progress:
                progress(message, fraction)

        asset_name = _asset_name()
        if asset_name is None:
            raise RuntimeError(
                f"There is no Ollama build for {platform.system()} {platform.machine()}."
            )

        report("Looking up the current Ollama release…")
        release = requests.get(RELEASE_API, timeout=30).json()
        tag = release.get("tag_name", "unknown")
        assets = {a.get("name"): a for a in release.get("assets", [])}
        if asset_name not in assets:
            raise RuntimeError(f"Release {tag} has no asset named {asset_name}.")

        expected = self._expected_digest(assets, asset_name)

        self.root.mkdir(parents=True, exist_ok=True)
        archive = self.root / asset_name
        url = assets[asset_name]["browser_download_url"]
        total = int(assets[asset_name].get("size", 0))

        report(f"Downloading Ollama {tag} ({total / 1048576:.0f} MB)…", 0.0)
        digest = hashlib.sha256()
        written = 0
        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with archive.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if total:
                        report(
                            f"Downloading Ollama {tag} — "
                            f"{written / 1048576:.0f} of {total / 1048576:.0f} MB",
                            written / total,
                        )

        if expected:
            actual = digest.hexdigest()
            if actual.lower() != expected.lower():
                archive.unlink(missing_ok=True)
                raise RuntimeError(
                    "The downloaded Ollama archive failed its checksum and was deleted. "
                    f"Expected {expected[:16]}…, got {actual[:16]}…"
                )
            report("Checksum verified.")
        else:
            # Not fatal — the release may not publish one — but the user
            # should know the guarantee was weaker than usual.
            logger.warning("No published checksum for %s; skipped verification.", asset_name)
            report("No published checksum for this asset; skipped verification.")

        report("Extracting…", -1.0)
        self._extract(archive, self.root)
        archive.unlink(missing_ok=True)

        binary = self._managed_binary()
        if binary is None:
            raise RuntimeError("The archive extracted but no ollama binary was found inside it.")
        if os.name != "nt":
            binary.chmod(binary.stat().st_mode | 0o111)

        report(f"Ollama {tag} is installed.", 1.0)
        return binary

    def _expected_digest(self, assets: dict, asset_name: str) -> str:
        """The published SHA-256 for this asset, or '' when unavailable."""
        checksums = assets.get("sha256sum.txt")
        if not checksums:
            return ""
        try:
            import requests

            body = requests.get(checksums["browser_download_url"], timeout=30).text
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not fetch the checksum file: %s", exc)
            return ""
        for line in body.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*").endswith(asset_name):
                return parts[0]
        return ""

    def _extract(self, archive: Path, destination: Path) -> None:
        name = archive.name.lower()
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(destination)
            return
        if name.endswith((".tgz", ".tar.gz")):
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(destination)
            return
        if name.endswith(".tar.zst"):
            self._extract_zst(archive, destination)
            return
        raise RuntimeError(f"Don't know how to extract {archive.name}.")

    def _extract_zst(self, archive: Path, destination: Path) -> None:
        """Zstandard is not in the standard library before Python 3.14, so
        try the optional package and fall back to the system tar, which
        supports zstd on any current Linux."""
        try:
            import zstandard  # type: ignore

            with archive.open("rb") as raw:
                reader = zstandard.ZstdDecompressor().stream_reader(raw)
                with tarfile.open(fileobj=reader, mode="r|") as tf:
                    tf.extractall(destination)
            return
        except ImportError:
            pass
        result = subprocess.run(
            ["tar", "--zstd", "-xf", str(archive), "-C", str(destination)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "This archive is zstd-compressed and neither the 'zstandard' package "
                "nor a zstd-capable tar is available. Install one with "
                f"`pip install zstandard`. tar said: {result.stderr.strip()[:200]}"
            )

    # -- server ---------------------------------------------------------------
    def start_server(self, base_url: str = "", timeout: float = 45.0) -> bool:
        """Starts `ollama serve` as a child process and waits for it to
        answer. Returns True once it is up."""
        base_url = base_url or config.OLLAMA_BASE_URL
        if self._list_models(base_url) is not None:
            return True  # already answering, ours or the user's

        binary, _managed = self.resolve_binary()
        if binary is None:
            return False

        with self._process_lock:
            if self._process is not None and self._process.poll() is None:
                pass  # ours is starting up
            else:
                creationflags = 0
                if os.name == "nt":
                    # Keep the console window from flashing up on Windows.
                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                logger.info("Starting the Ollama server from %s", binary)
                self._process = subprocess.Popen(
                    [str(binary), "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._list_models(base_url) is not None:
                logger.info("Ollama server is answering.")
                return True
            time.sleep(0.7)

        logger.warning("The Ollama server did not answer within %.0fs.", timeout)
        return False

    def stop_server(self) -> None:
        """Stops the server, but only if Lekha started it. A server the
        user was already running is not ours to shut down."""
        with self._process_lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        logger.info("Stopping the Ollama server Lekha started.")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    def owns_server(self) -> bool:
        with self._process_lock:
            return self._process is not None and self._process.poll() is None

    # -- models ----------------------------------------------------------------
    def pull_model(self, model: str, base_url: str = "",
                   progress: Optional[ProgressCallback] = None) -> bool:
        """Pulls a model, reporting progress from Ollama's own stream."""
        import json

        import requests

        base_url = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        try:
            with requests.post(
                f"{base_url}/api/pull",
                json={"model": model, "stream": True},
                stream=True,
                timeout=(15, 1800),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise RuntimeError(event["error"])
                    status = event.get("status", "")
                    completed = event.get("completed")
                    total = event.get("total")
                    fraction = (completed / total) if (completed and total) else -1.0
                    if progress:
                        if completed and total:
                            progress(
                                f"{status} — {completed / 1048576:.0f} of "
                                f"{total / 1048576:.0f} MB",
                                fraction,
                            )
                        else:
                            progress(status or "Pulling…", fraction)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Model pull failed: %s", exc)
            if progress:
                progress(f"Pull failed: {exc}", -1.0)
            return False

        logger.info("Model %s is available.", model)
        return True

    def uninstall(self) -> None:
        """Removes the managed install. Never touches a system install."""
        self.stop_server()
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)
            logger.info("Removed the managed Ollama install.")


ai_runtime = AIRuntime.instance()


def _shutdown() -> None:
    """Stops a server Lekha started, when Lekha exits.

    Registered once, at import. Streamlit re-executes the script on every
    interaction but module state is cached, so this does not accumulate
    handlers. A server the user started themselves is left alone.
    """
    try:
        ai_runtime.stop_server()
    except Exception:  # noqa: BLE001
        pass


atexit.register(_shutdown)
