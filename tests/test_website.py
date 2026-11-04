"""Serving and building the website (website.py, the catch-all route, /api/admin/website): no database or models needed."""

import os
import sys
import time
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("torch")  # api_server imports the search stack

from fastapi.testclient import TestClient

import api_server
import runtime_config
import website
from config import settings

TOKEN = "t" * 40
ADMIN = {"X-Admin-Token": TOKEN}


@asynccontextmanager
async def _no_lifespan(_app):
    yield


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A tiny built site in a temp folder, the switch off, settings and state isolated."""
    dist = tmp_path / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>OSISS</title><div id=root></div>")
    (dist / "assets" / "app-abc123.js").write_text("console.log('app')")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "secret.txt").write_text("outside the site")
    monkeypatch.setattr(website, "DIST", str(dist))
    monkeypatch.setattr(website, "FRONTEND_DIR", str(tmp_path / "frontend"))
    saved = {key: getattr(settings, key) for key in ("admin_token", "admin_allowed_ips", "settings_overrides_path", "serve_frontend", "public_base_url", "search_access")}
    object.__setattr__(settings, "admin_token", TOKEN)
    object.__setattr__(settings, "admin_allowed_ips", "")
    object.__setattr__(settings, "settings_overrides_path", str(tmp_path / "admin" / "overrides.json"))
    object.__setattr__(settings, "serve_frontend", False)
    object.__setattr__(settings, "public_base_url", "")
    object.__setattr__(settings, "search_access", "")
    runtime_config._state.clear()
    monkeypatch.setattr(api_server.app.router, "lifespan_context", _no_lifespan)
    monkeypatch.setattr(website, "builder", website.Builder())
    with TestClient(api_server.app) as client:
        client.dist = dist
        yield client
    for key, value in saved.items():
        object.__setattr__(settings, key, value)
    runtime_config._state.clear()


def go_live(client):
    return client.put("/api/admin/website", json={"live": True}, headers=ADMIN)


def test_offline_by_default_then_served_when_live(site):
    assert site.get("/").status_code == 404 and site.get("/assets/app-abc123.js").status_code == 404
    assert go_live(site).json()["live"] is True
    home = site.get("/")
    assert home.status_code == 200 and "<div id=root>" in home.text
    assert home.headers["cache-control"] == "no-cache" and home.headers["x-content-type-options"] == "nosniff"
    asset = site.get("/assets/app-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"] and "javascript" in asset.headers["content-type"]
    assert site.get("/favicon.svg").headers["cache-control"] == "public, max-age=3600"
    assert site.get("/index.html").status_code == 200
    assert site.get("/some/page").text == home.text  # the single-page app handles its own addresses
    assert site.get("/missing.js").status_code == 404 and site.get("/assets/nope.css").status_code == 404

    assert site.put("/api/admin/website", json={"live": False}, headers=ADMIN).json()["live"] is False
    assert site.get("/").status_code == 404  # offline again, at once, no restart


def test_the_switch_is_saved_and_the_environment_only_sets_its_default(site):
    assert site.get("/api/admin/website", headers=ADMIN).json()["live"] is False
    object.__setattr__(settings, "serve_frontend", True)
    assert site.get("/api/admin/website", headers=ADMIN).json()["live"] is True  # default from SERVE_FRONTEND
    site.put("/api/admin/website", json={"live": False}, headers=ADMIN)
    assert site.get("/").status_code == 404  # the admin's choice wins over the default
    assert '"website_live": false' in open(settings.settings_overrides_path).read()
    runtime_config._state.clear()  # a restart reads it back
    runtime_config.load_overrides()
    assert website.is_live() is False


def test_files_outside_the_build_are_never_served(site):
    go_live(site)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt", "/..%2fsecret.txt", "//etc/passwd"):
        response = site.get(path)
        assert "outside the site" not in response.text, path
        assert response.status_code in (200, 404) and (response.status_code == 404 or "<div id=root>" in response.text), path
    assert website.resolve_file("../secret.txt") is None and website.resolve_file("assets/../../secret.txt") is None
    link = site.dist / "leak.txt"
    link.symlink_to(site.dist.parent.parent / "secret.txt")
    assert site.get("/leak.txt").status_code == 404  # a symlink out of the build is refused


def test_api_paths_keep_their_json_errors_and_routes(site):
    go_live(site)
    for path in ("/api/nothing", "/api/workspace/nothing", "/data/nothing", "/health/x"):
        response = site.get(path)
        assert response.status_code == 404 and response.json()["detail"].lower() == "not found", path
    assert site.get("/health").status_code == 200
    assert site.get("/api/auth/config").status_code == 200  # real routes still win over the catch-all


def test_going_live_needs_a_build(site):
    (site.dist / "index.html").unlink()
    response = go_live(site)
    assert response.status_code == 409 and response.json()["detail"] == "Build the website first."
    assert site.get("/api/admin/website", headers=ADMIN).json()["built"] is False
    assert site.put("/api/admin/website", json={"live": False}, headers=ADMIN).status_code == 200  # offline is always allowed


def test_status_reports_the_build(site):
    status = site.get("/api/admin/website", headers=ADMIN).json()
    assert status["built"] is True and status["files"] == 3 and status["size_bytes"] > 0 and status["built_at"].endswith("+00:00")
    assert status["build"]["state"] == "idle" and "node" in status and status["search_access"] == "open"


def test_admin_website_routes_need_the_token(site):
    for call in (site.get("/api/admin/website"), site.post("/api/admin/website/build"), site.put("/api/admin/website", json={"live": True})):
        assert call.status_code == 401


# --- building -----------------------------------------------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self._code = code

    def wait(self):
        return self._code

    def kill(self):
        self._code = -9


def fake_builder(tmp_path, results, record):
    """A Builder whose npm calls are scripted: results maps the first npm argument to (lines, exit code)."""

    def popen(command, **kwargs):
        record.append(command[1:])
        lines, code = results[command[1]]
        return FakeProcess(lines, code)

    return website.Builder(popen=popen)


def test_a_build_installs_when_needed_then_builds(site, tmp_path, monkeypatch):
    calls = []
    builder = fake_builder(tmp_path, {"install": (["added 10 packages\n"], 0), "run": (["vite built in 1s\n"], 0)}, calls)
    monkeypatch.setattr(website, "builder", builder)
    monkeypatch.setattr(website, "node_info", lambda: {"npm": "/usr/bin/npm", "node": "v22.12.0", "ok": True, "message": ""})
    assert site.post("/api/admin/website/build", headers=ADMIN).status_code == 200
    builder.wait()
    assert calls == [["install", "--no-audit", "--no-fund"], ["run", "build"]]  # no node_modules in the temp folder yet
    status = site.get("/api/admin/website", headers=ADMIN).json()["build"]
    assert status["state"] == "ok" and status["log"] == ["$ npm install --no-audit --no-fund", "added 10 packages", "$ npm run build", "vite built in 1s"]
    assert status["finished_at"] >= status["started_at"]

    modules = tmp_path / "frontend" / "node_modules"
    modules.mkdir()
    (modules / ".package-lock.json").write_text("{}")
    (tmp_path / "frontend" / "package-lock.json").write_text("{}")
    os.utime(tmp_path / "frontend" / "package-lock.json", (time.time() - 100, time.time() - 100))
    calls.clear()
    builder.start("/usr/bin/npm")
    builder.wait()
    assert calls == [["run", "build"]]  # dependencies are up to date: straight to the build
    os.utime(tmp_path / "frontend" / "package-lock.json", (time.time() + 100, time.time() + 100))
    calls.clear()
    builder.start("/usr/bin/npm")
    builder.wait()
    assert calls[0][0] == "install"  # a newer package-lock.json means install again


def test_colour_codes_are_removed_from_the_log(tmp_path):
    builder = fake_builder(tmp_path, {"install": ([], 0), "run": (["\x1b[2mdist/\x1b[0m\x1b[32mindex.html\x1b[39m  0.6 kB\n"], 0)}, [])
    builder.start("/usr/bin/npm")
    builder.wait()
    assert "dist/index.html  0.6 kB" in builder.status()["log"]


def test_a_failed_build_keeps_its_log(site, tmp_path, monkeypatch):
    builder = fake_builder(tmp_path, {"install": ([], 0), "run": (["error TS2322: bad type\n"], 2)}, [])
    monkeypatch.setattr(website, "builder", builder)
    monkeypatch.setattr(website, "node_info", lambda: {"npm": "/usr/bin/npm", "node": "v22.12.0", "ok": True, "message": ""})
    site.post("/api/admin/website/build", headers=ADMIN)
    builder.wait()
    status = site.get("/api/admin/website", headers=ADMIN).json()["build"]
    assert status["state"] == "failed" and status["message"] == "The build failed; see the log." and "error TS2322: bad type" in status["log"]


def test_one_build_at_a_time_and_a_clear_message_without_node(site, tmp_path, monkeypatch):
    gate = {}

    class Slow(FakeProcess):
        def wait(self):
            gate["release"].wait(5)
            return 0

    import threading

    gate["release"] = threading.Event()
    slow = website.Builder(popen=lambda command, **kw: Slow(["working\n"]))
    monkeypatch.setattr(website, "builder", slow)
    monkeypatch.setattr(website, "node_info", lambda: {"npm": "/usr/bin/npm", "node": "v22.12.0", "ok": True, "message": ""})
    assert site.post("/api/admin/website/build", headers=ADMIN).status_code == 200
    second = site.post("/api/admin/website/build", headers=ADMIN)
    assert second.status_code == 409 and second.json()["detail"] == "A build is already running."
    assert site.get("/api/admin/website", headers=ADMIN).json()["build"]["state"] == "running"
    gate["release"].set()
    slow.wait()

    monkeypatch.setattr(website, "node_info", lambda: {"npm": None, "node": None, "ok": False, "message": "Node.js (npm) was not found on the server."})
    missing = site.post("/api/admin/website/build", headers=ADMIN)
    assert missing.status_code == 503 and "Node.js" in missing.json()["detail"]


def test_npm_that_cannot_start_is_a_failed_build(tmp_path, monkeypatch):
    monkeypatch.setattr(website, "FRONTEND_DIR", str(tmp_path))

    def broken(command, **kwargs):
        raise FileNotFoundError("npm")

    builder = website.Builder(popen=broken)
    builder.start("/nonexistent/npm")
    builder.wait()
    assert builder.state == "failed" and any("Could not run npm" in line for line in builder.status()["log"])


def test_node_version_rules():
    assert website.parse_node_version("v22.12.0") == (22, 12, 0) and website.parse_node_version("nonsense") is None
    for good in ("v20.19.0", "v20.20.1", "v22.12.0", "v22.23.1", "v24.0.0"):
        assert website.node_supported(website.parse_node_version(good)), good
    for bad in ("v18.20.0", "v20.18.9", "v22.11.0", "v21.7.0", ""):
        assert not website.node_supported(website.parse_node_version(bad)), bad


def test_nvm_installs_are_found_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(website.shutil, "which", lambda name: None)
    for version in ("v18.0.0", "v22.23.1", "v22.4.0"):
        folder = tmp_path / ".nvm" / "versions" / "node" / version / "bin"
        folder.mkdir(parents=True)
        (folder / "npm").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert website.find_npm().endswith("v22.23.1/bin/npm")
    monkeypatch.setattr(website.glob, "glob", lambda pattern: [])
    assert website.find_npm() is None
    info = website.node_info()
    assert info["ok"] is False and "Node.js" in info["message"]
