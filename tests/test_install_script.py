"""site/install.sh: the server installer. Needs only bash; nothing is installed or downloaded.

System changes are checked with --dry-run (plus fake apt-get/sudo/... programs that would record any real call), and
the part that does run for real (download, extract, setup.sh hand-off, .env) is checked against a local tarball.
"""

import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "site" / "install.sh"
EXIT_ROOT_NEEDED, EXIT_UNSUPPORTED = 10, 20

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")


def bash(*args, env=None, stdin=None, cwd=None):
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(["bash", *args], capture_output=True, text=True, env=full_env, input=stdin, cwd=cwd, timeout=60)


def install(*args, env=None, stdin=None):
    return bash(str(SCRIPT), *args, env=env, stdin=stdin)


def sourced(snippet, env=None):
    """Run a snippet with the script's functions loaded (main does not run when sourced)."""
    return bash("-c", f'source "{SCRIPT}"; {snippet}', env=env)


@pytest.fixture
def ubuntu(tmp_path):
    release = tmp_path / "os-release"
    release.write_text('ID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n')
    return {"OSISS_OS_RELEASE": str(release), "OSISS_UNAME": "Linux"}


@pytest.fixture
def fake_tools(tmp_path):
    """Fake apt-get, systemctl, ... first on PATH. Any real call would leave a line in `calls`."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for name in ("apt-get", "systemctl", "useradd", "usermod", "chown", "runuser"):  # tools that change the system
        tool = bin_dir / name
        tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nexit 1\n')
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}"}, calls


# ---- the file itself -------------------------------------------------------------------------------------------


def test_syntax_is_valid_and_old_bash_features_are_avoided():
    assert bash("-n", str(SCRIPT)).returncode == 0
    text = SCRIPT.read_text()
    # macOS ships bash 3.2: no associative arrays, mapfile or case-conversion expansions.
    for modern in ("declare -A", "mapfile", "readarray", ",,}", "^^}"):
        assert modern not in text, modern


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_shellcheck_is_clean():
    result = subprocess.run(["shellcheck", "-s", "bash", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


def test_help_lists_every_option():
    result = install("--help")
    assert result.returncode == 0
    for option in ("--dir", "--ref", "--listen", "--service", "--no-website", "--no-print-token", "--user", "--check", "--dry-run"):
        assert option in result.stdout, option


@pytest.mark.parametrize(
    "args, message",
    [
        (["--listen", "everyone"], "--listen must be local or all"),
        (["--dir", "relative/path"], "absolute"),
        (["--dir", "/usr"], "Refusing"),
        (["--dir", "/"], "Refusing"),
        (["--user", "Bad User"], "Invalid user"),
        (["--bogus"], "Unknown option"),
        (["--dir"], "--dir needs a value"),
    ],
)
def test_bad_arguments_are_refused(args, message):
    result = install(*args)
    assert result.returncode != 0 and message in result.stderr


# ---- which systems it accepts ----------------------------------------------------------------------------------


def test_windows_is_refused_with_a_pointer_to_the_admin_app():
    result = install("--check", env={"OSISS_UNAME": "MINGW64_NT-10.0"})
    assert result.returncode == EXIT_UNSUPPORTED
    assert "does not run on Windows" in result.stderr and "admin app" in result.stderr


def test_non_debian_linux_is_refused(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=fedora\nID_LIKE=rhel\n")
    result = install("--check", env={"OSISS_UNAME": "Linux", "OSISS_OS_RELEASE": str(release)})
    assert result.returncode == EXIT_UNSUPPORTED and "Ubuntu and Debian" in result.stderr


def test_debian_family_is_accepted(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=linuxmint\nID_LIKE=\"ubuntu debian\"\n")
    out = sourced('OSISS_UNAME=Linux; detect_os; echo "$OS"', env={"OSISS_OS_RELEASE": str(release)})
    assert out.stdout.strip() == "debian"


def test_macos_without_homebrew_and_docker_lists_what_to_install(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    for tool in ("bash", "uname", "id", "dirname", "tr", "grep", "cat"):  # nothing else, so no brew and no docker
        os.symlink(shutil.which(tool), empty / tool)
    result = install("--check", env={"OSISS_UNAME": "Darwin", "PATH": str(empty)})
    assert result.returncode == EXIT_UNSUPPORTED
    assert "Homebrew" in result.stdout and "Docker Desktop" in result.stdout


# ---- what needs administrator rights ---------------------------------------------------------------------------

ALL_PRESENT = (
    "have_pkg() { return 0; }; have_compose() { return 0; }; node_ok() { return 0; }; user_exists() { return 0; }; "
    "in_docker_group() { return 0; }; dir_usable() { return 0; }; command() { builtin command \"$@\"; }; "
)


ALL_MISSING = (
    "have_pkg() { return 1; }; have_compose() { return 1; }; node_ok() { return 1; }; user_exists() { return 1; }; "
    "in_docker_group() { return 1; }; dir_usable() { return 1; }; "
)


def test_nothing_missing_means_no_administrator_step(ubuntu):
    result = sourced(ALL_PRESENT + "main --check --dir /srv/o --user alice", env=ubuntu)
    assert result.returncode == 0 and result.stdout == "", result.stdout + result.stderr


def test_each_missing_piece_is_reported_and_check_exits_10(ubuntu):
    cases = {
        "have_pkg() { return 1; }": "system packages:",
        "have_compose() { return 1; }": "Docker",
        "node_ok() { return 1; }": "Node.js",
        "user_exists() { return 1; }": "the user alice",
        "dir_usable() { return 1; }": "the folder /srv/o",
    }
    for stub, expected in cases.items():
        result = sourced(
            ALL_PRESENT + f"{stub}; detect_os; DIR=/srv/o; TARGET_USER=alice; root_reasons", env=ubuntu
        )
        assert expected in result.stdout, (stub, result.stdout)
    check = sourced(ALL_PRESENT + "have_compose() { return 1; }; main --check --dir /srv/o --user alice", env=ubuntu)
    assert "Docker" in check.stdout and check.returncode == EXIT_ROOT_NEEDED


def test_no_website_skips_node(ubuntu):
    result = sourced(ALL_PRESENT + "node_ok() { return 1; }; detect_os; DIR=/srv/o; TARGET_USER=alice; NO_WEBSITE=1; root_reasons", env=ubuntu)
    assert "Node" not in result.stdout


def test_service_is_listed_as_an_administrator_step(ubuntu):
    result = sourced(ALL_PRESENT + "detect_os; DIR=/srv/o; TARGET_USER=alice; SERVICE=1; root_reasons", env=ubuntu)
    assert "systemd service" in result.stdout


# ---- dry runs change nothing -----------------------------------------------------------------------------------


def test_dry_run_prints_the_plan_and_calls_no_system_tool(ubuntu, fake_tools, tmp_path):
    path_env, calls = fake_tools
    env = {**ubuntu, **path_env}
    result = install("--dry-run", "--listen", "all", "--service", "--dir", str(tmp_path / "osiss"), "--user", "osiss", env=env)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "--phase root" in out and "--phase user" in out and "--phase start" in out
    assert out.index("--phase root") < out.index("--phase user") < out.index("--phase start")
    assert not calls.exists(), calls.read_text() if calls.exists() else ""  # no real apt-get/sudo/systemctl/... call
    assert not (tmp_path / "osiss").exists()


def test_root_phase_dry_run_lists_the_commands(ubuntu, fake_tools, tmp_path):
    path_env, calls = fake_tools
    out = sourced(
        ALL_MISSING + f"main --dry-run --phase root --service --dir {tmp_path / 'o'} --user osiss", env={**ubuntu, **path_env}
    ).stdout
    assert "+ useradd -m -s /bin/bash osiss" in out
    assert "+ apt-get update" in out and "tesseract-ocr-ben" in out and "tesseract-ocr-hin" in out
    assert "get.docker.com" in out and "setup_22.x" in out
    assert "usermod -aG docker osiss" in out and "osiss-api.service" in out and "systemctl enable osiss-api" in out
    assert not calls.exists()


def test_root_phase_with_everything_present_does_nothing(ubuntu, fake_tools, tmp_path):
    path_env, calls = fake_tools
    out = sourced(ALL_PRESENT + f"main --dry-run --phase root --dir {tmp_path / 'o'} --user osiss", env={**ubuntu, **path_env}).stdout
    assert "No administrator step needed" in out and "apt-get" not in out and not calls.exists()


def test_no_website_dry_run_never_mentions_node(ubuntu, fake_tools, tmp_path):
    path_env, _ = fake_tools
    out = sourced(
        ALL_MISSING + f"main --dry-run --phase root --no-website --dir {tmp_path / 'o'} --user osiss", env={**ubuntu, **path_env}
    ).stdout
    assert "apt-get update" in out and "setup_22.x" not in out and "nodejs" not in out


def test_user_phase_dry_run_uses_the_requested_ref_and_folder(ubuntu, tmp_path):
    result = install("--dry-run", "--phase", "user", "--ref", "v9.9", "--dir", str(tmp_path / "o"), "--user", "x", env=ubuntu)
    assert "/OSISS/tar.gz/v9.9" in result.stdout and "setup.sh" in result.stdout


# ---- the part that really runs: download, extract, setup.sh, .env ----------------------------------------------


@pytest.fixture
def archive(tmp_path):
    """A local stand-in for the GitHub tarball: <base>/<owner>/<repo>/tar.gz/<ref> with a fake setup.sh."""
    root = tmp_path / "pkg" / "OSISS-main"
    (root / "data").mkdir(parents=True)
    (root / "models").mkdir()
    (root / "data" / ".gitkeep").write_text("")
    (root / "api.sh").write_text("echo api\n")
    (root / "setup.sh").write_text(
        '#!/usr/bin/env bash\necho "setup ran in $(pwd)"\n'
        '[ -f .env ] || { touch .env; chmod 600 .env; echo "ADMIN_TOKEN=secret_token_value_0123456789" >> .env; }\n'
    )
    base = tmp_path / "base"
    target = base / "midyadebangshu-spec" / "OSISS" / "tar.gz"
    target.mkdir(parents=True)
    for ref in ("main", "v2"):
        with tarfile.open(target / ref, "w:gz") as tar:
            tar.add(root, arcname="OSISS-main")
    return {"OSISS_ARCHIVE_BASE": f"file://{base}"}


def run_user_phase(folder, env, *extra):
    return install("--phase", "user", "--user", "tester", "--dir", str(folder), *extra, env=env)


def test_first_install_downloads_runs_setup_and_prints_the_token(ubuntu, archive, tmp_path):
    folder = tmp_path / "my server"  # a space in the path must be fine
    result = run_user_phase(folder, {**ubuntu, **archive}, "--listen", "all")
    assert result.returncode == 0, result.stderr
    assert (folder / "api.sh").exists() and (folder / "setup.sh").exists()
    assert f"setup ran in {folder}" in result.stdout  # setup.sh runs from the install folder
    assert "secret_token_value_0123456789" in result.stdout
    assert "http://localhost:8000" in result.stdout and ":8000" in result.stdout
    env_file = folder / ".env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert "API_HOST=0.0.0.0" in env_file.read_text()


def test_listen_local_is_the_default_and_the_token_can_be_hidden(ubuntu, archive, tmp_path):
    result = run_user_phase(tmp_path / "o", {**ubuntu, **archive}, "--no-print-token")
    assert "secret_token_value_0123456789" not in result.stdout and "secret_token_value_0123456789" not in result.stderr
    assert "API_HOST=127.0.0.1" in (tmp_path / "o" / ".env").read_text()
    assert "--listen all" in result.stdout  # tells you how to open it up


def test_rerun_keeps_env_data_and_models_but_refreshes_code(ubuntu, archive, tmp_path):
    folder = tmp_path / "o"
    env = {**ubuntu, **archive}
    run_user_phase(folder, env)
    (folder / "data" / "mine.pdf").write_text("my book")
    (folder / "models" / "m.bin").write_text("weights")
    (folder / "api.sh").write_text("locally damaged\n")
    token_line = next(line for line in (folder / ".env").read_text().splitlines() if line.startswith("ADMIN_TOKEN="))
    result = run_user_phase(folder, env, "--listen", "all")
    assert result.returncode == 0 and "Updating the existing install" in result.stdout
    assert (folder / "data" / "mine.pdf").read_text() == "my book" and (folder / "models" / "m.bin").exists()
    assert (folder / "api.sh").read_text() == "echo api\n"  # code refreshed
    lines = (folder / ".env").read_text().splitlines()
    assert token_line in lines and sum(line.startswith("API_HOST=") for line in lines) == 1  # no duplicates
    assert "API_HOST=0.0.0.0" in lines


def test_unknown_ref_fails_cleanly(ubuntu, archive, tmp_path):
    result = run_user_phase(tmp_path / "o", {**ubuntu, **archive}, "--ref", "does-not-exist")
    assert result.returncode != 0


def test_ref_selects_the_download(ubuntu, archive, tmp_path):
    assert run_user_phase(tmp_path / "o", {**ubuntu, **archive}, "--ref", "v2").returncode == 0


# ---- curl | bash -----------------------------------------------------------------------------------------------


def test_piped_into_bash_it_saves_a_copy_of_itself(ubuntu, fake_tools, tmp_path):
    path_env, calls = fake_tools
    env = {**ubuntu, **path_env, "OSISS_INSTALL_URL": f"file://{SCRIPT}", "TMPDIR": str(tmp_path)}
    result = bash("-s", "--", "--dry-run", "--dir", str(tmp_path / "o"), "--user", "osiss", "--service", env=env, stdin=SCRIPT.read_text())
    assert result.returncode == 0, result.stderr
    assert "--phase root" in result.stdout and "/osiss-install." in result.stdout  # it calls its saved copy
    assert not calls.exists()
