"""The public website (frontend/dist): build it, serve it, switch it on and off. No models or databases.

The API serves the built site from `frontend/dist` while the "live" switch is on. The switch is server state saved by the
admin app (`website_live`); the SERVE_FRONTEND environment variable only sets its default, which is off. Because the
files and the switch are read on every request, a new build or a switch change takes effect at once, with no restart.

Building needs Node.js (npm) on the server. The build runs in a background thread; its output is kept for the admin app.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional

from config import settings

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
DIST = os.path.join(FRONTEND_DIR, "dist")
MIN_NODE = ((20, 19), (22, 12))  # Vite 8: Node 20.19+ or 22.12+
LOG_LINES = 400
BUILD_TIMEOUT = 15 * 60
API_PREFIXES = ("api/", "data/", "health/", "ready/")
API_PATHS = ("health", "ready", "api", "data")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # colour codes some tools print even without a terminal


# --- the switch -----------------------------------------------------------------------------------------------------------


def is_live() -> bool:
    import runtime_config  # pylint: disable=import-outside-toplevel

    return bool(runtime_config.get_state("website_live", settings.serve_frontend))


def set_live(live: bool) -> None:
    import runtime_config  # pylint: disable=import-outside-toplevel

    runtime_config.set_state("website_live", bool(live))


# --- what is built --------------------------------------------------------------------------------------------------------


def built_info() -> Dict[str, Any]:
    """Whether a usable build exists (frontend/dist/index.html), when it was made and how big it is."""
    index = os.path.join(DIST, "index.html")
    if not os.path.isfile(index):
        return {"built": False, "built_at": None, "files": 0, "size_bytes": 0}
    files = size = 0
    for folder, _, names in os.walk(DIST):
        for name in names:
            files += 1
            size += os.path.getsize(os.path.join(folder, name))
    stamp = datetime.fromtimestamp(os.path.getmtime(index), tz=timezone.utc)
    return {"built": True, "built_at": stamp.isoformat(), "files": files, "size_bytes": size}


def resolve_file(path: str) -> Optional[str]:
    """The file a URL path maps to inside the build, or None (never anything outside `frontend/dist`)."""
    root = os.path.realpath(DIST)
    if not os.path.isdir(root):
        return None
    clean = path.strip("/")
    target = os.path.realpath(os.path.join(root, clean)) if clean else os.path.join(root, "index.html")
    if os.path.isdir(target):
        target = os.path.join(target, "index.html")
    inside = target == root or target.startswith(root + os.sep)
    if inside and os.path.isfile(target):
        return target
    return None


def is_api_path(path: str) -> bool:
    clean = path.strip("/")
    return clean in API_PATHS or clean.startswith(API_PREFIXES)


def cache_headers(path: str) -> Dict[str, str]:
    """index.html is never cached (so a new build shows at once); hashed assets are cached for a year."""
    clean = path.strip("/")
    if clean.startswith("assets/"):
        return {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
    if clean in ("", "index.html") or not os.path.splitext(clean)[1]:
        return {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    return {"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"}


# --- Node ---------------------------------------------------------------------------------------------------------------------


def find_npm() -> Optional[str]:
    """npm on PATH, else the newest one of an nvm install (a service started from a menu has a short PATH)."""
    found = shutil.which("npm")
    if found:
        return found
    candidates = glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/npm"))
    return max(candidates, key=_version_key, default=None)


def _version_key(path: str):
    match = re.search(r"v(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def parse_node_version(text: str) -> Optional[tuple]:
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def node_supported(version: Optional[tuple]) -> bool:
    if version is None:
        return False
    major, minor = version[0], version[1]
    return any(major == m and minor >= n for m, n in MIN_NODE) or major > 22


def node_info(npm: Optional[str] = None) -> Dict[str, Any]:
    """{"npm": path or None, "node": version text or None, "ok": whether it can build the site, "message"}."""
    npm = npm or find_npm()
    if not npm:
        return {"npm": None, "node": None, "ok": False,
                "message": "Node.js (npm) was not found on the server. Install Node.js 20.19+ or 22.12+ to build the website here."}
    node = os.path.join(os.path.dirname(npm), "node")
    try:
        text = subprocess.run([node if os.path.exists(node) else "node", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # pylint: disable=broad-except
        text = ""
    version = parse_node_version(text)
    if not node_supported(version):
        return {"npm": npm, "node": text or None, "ok": False,
                "message": f"Node.js {text or '(unknown version)'} is too old. The website needs Node.js 20.19+ or 22.12+."}
    return {"npm": npm, "node": text, "ok": True, "message": ""}


# --- building ---------------------------------------------------------------------------------------------------------------------


class Builder:
    """Runs `npm install` (when needed) and `npm run build` in frontend/, one build at a time, keeping the output."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen, clock: Callable[[], float] = time.time):
        self._popen = popen
        self._clock = clock
        self._lock = threading.Lock()
        self._log: Deque[str] = deque(maxlen=LOG_LINES)
        self.state = "idle"  # idle | running | ok | failed
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.message = ""
        self._thread: Optional[threading.Thread] = None

    def needs_install(self) -> bool:
        modules = os.path.join(FRONTEND_DIR, "node_modules")
        lock = os.path.join(FRONTEND_DIR, "package-lock.json")
        marker = os.path.join(modules, ".package-lock.json")
        if not os.path.isdir(modules) or not os.path.exists(marker):
            return True
        return os.path.exists(lock) and os.path.getmtime(lock) > os.path.getmtime(marker)

    def start(self, npm: str) -> bool:
        """Begin a build in the background. False when one is already running."""
        with self._lock:
            if self.state == "running":
                return False
            self.state, self.message = "running", ""
            self.started_at, self.finished_at = self._clock(), None
            self._log.clear()
        self._thread = threading.Thread(target=self._run, args=(npm,), name="osiss-website-build", daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float = 30.0) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _command(self, args: List[str], npm: str) -> bool:
        self._log.append(f"$ npm {' '.join(args)}")
        env = dict(os.environ, CI="1", NO_COLOR="1")
        env["PATH"] = os.path.dirname(npm) + os.pathsep + env.get("PATH", "")  # node next to this npm
        try:
            process = self._popen([npm, *args], cwd=FRONTEND_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            self._log.append(f"Could not run npm: {exc}")
            return False
        deadline = self._clock() + BUILD_TIMEOUT
        for line in process.stdout or []:
            self._log.append(ANSI.sub("", line).rstrip())
            if self._clock() > deadline:
                process.kill()
                self._log.append("The build took more than 15 minutes and was stopped.")
                return False
        return process.wait() == 0

    def _run(self, npm: str) -> None:
        ok = True
        try:
            if self.needs_install():
                ok = self._command(["install", "--no-audit", "--no-fund"], npm)
            if ok:
                ok = self._command(["run", "build"], npm)
        except Exception as exc:  # pylint: disable=broad-except
            self._log.append(f"Build failed: {exc}")
            ok = False
        with self._lock:
            self.state = "ok" if ok else "failed"
            self.message = "" if ok else "The build failed; see the log."
            self.finished_at = self._clock()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state, "message": self.message, "started_at": self.started_at,
                "finished_at": self.finished_at, "log": list(self._log),
            }


builder = Builder()


def status() -> Dict[str, Any]:
    """Everything the admin app's Website page shows."""
    return {"live": is_live(), **built_info(), "node": node_info(), "build": builder.status()}
