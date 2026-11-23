"""install-on-mynode.sh with stubbed mynode-manage-apps and systemctl."""
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "mynode-app" / "xpubwatch"
VERSION = (APP / "app_data" / "VERSION").read_text().strip()


@pytest.fixture
def node(tmp_path):
    apps = tmp_path / "usr/share/mynode_apps"
    apps.mkdir(parents=True)
    hdd = tmp_path / "mnt/hdd/mynode"
    hdd.mkdir(parents=True)
    vfile = tmp_path / "home/bitcoin/.mynode/xpubwatch_version"
    vfile.parent.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    # mynode-manage-apps stub: records calls; install/reinstall writes the version file
    # like MyNode does after a successful install script (or "error").
    (bin_dir / "mynode-manage-apps").write_text(f"""#!/bin/bash
echo "manage $*" >> {log}
if [ "$1" = install ] || [ "$1" = reinstall ]; then
  test -f {apps}/xpubwatch/xpubwatch.json || {{ echo error > {vfile}; exit 0; }}
  if [ -n "$FAIL_INSTALL" ]; then echo error > {vfile}; else cat {apps}/xpubwatch/app_data/VERSION > {vfile}; fi
fi
""")
    data = hdd / "xpubwatch"
    # systemctl stub: "restart" behaves like the app starting (creates the database).
    (bin_dir / "systemctl").write_text(f"""#!/bin/bash
echo "systemctl $*" >> {log}
[ "$1" = is-active ] && [ -n "$SERVICE_DOWN" ] && exit 3
if [ "$1" = restart ] && [ -z "$NO_DB" ]; then
  mkdir -p {data}
  python3 - <<'PY'
import os, sqlite3
c = sqlite3.connect("{data}/xpubwatch.db")
c.execute("CREATE TABLE IF NOT EXISTS wallets (id INTEGER PRIMARY KEY, name TEXT)")
if os.environ.get("SHRINK"):
    c.execute("DELETE FROM wallets")
c.commit()
PY
fi
exit 0
""")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH="%s:%s" % (bin_dir, os.environ["PATH"]),
               XPUBW_APPS_DIR=str(apps), XPUBW_DATA_DIR=str(hdd / "xpubwatch"),
               XPUBW_VERSION_FILE=str(vfile), XPUBW_WAIT="1")
    # Extract the real release layout.
    out = tmp_path / "dist"
    subprocess.run(["python3", str(ROOT / "scripts" / "build-release.py"), str(out)], check=True, capture_output=True)
    with tarfile.open(next(out.glob("*.tar.gz"))) as t:
        if hasattr(tarfile, "data_filter"):
            t.extractall(tmp_path / "tmp", filter="data")
        else:
            t.extractall(tmp_path / "tmp")
    return {"apps": apps, "hdd": hdd, "vfile": vfile, "log": log, "env": env,
            "script": tmp_path / "tmp" / "xpubwatch" / "install-on-mynode.sh"}


def run(node, *args, **env):
    e = dict(node["env"], **env)
    return subprocess.run(["bash", str(node["script"])] + list(args), env=e, capture_output=True, text=True)


def calls(node):
    return node["log"].read_text().splitlines() if node["log"].exists() else []


def test_fresh_install(node):
    r = run(node)
    assert r.returncode == 0, r.stderr + r.stdout
    target = node["apps"] / "xpubwatch"
    assert (target / "xpubwatch.json").exists() and (target / "app_data" / "telegram_bot.py").exists()
    assert (target / "is_manually_added").exists()
    assert not (target / "install-on-mynode.sh").exists()
    assert calls(node) == ["manage init", "manage install xpubwatch", "systemctl enable xpubwatch",
                           "systemctl restart xpubwatch", "systemctl is-active --quiet xpubwatch",
                           "systemctl is-active --quiet xpubwatch"]
    assert "installed and running" in r.stdout and ":5116" in r.stdout
    assert "Existing data: none (new installation)" in r.stdout
    assert "Data after installation: 0 wallet(s)" in r.stdout
    assert not list(node["hdd"].glob("xpubwatch-backup-*")), "nothing to back up on a fresh install"


def test_upgrade_backs_up_and_same_version_reinstalls(node):
    data = node["hdd"] / "xpubwatch"
    data.mkdir()
    make_db(data, 3)
    node["vfile"].write_text("0.4.0-beta.1\n")
    (node["apps"] / "xpubwatch").mkdir()
    (node["apps"] / "xpubwatch" / "stale-file").write_text("x")
    r = run(node)
    assert r.returncode == 0, r.stderr
    assert "Existing data: 3 wallet(s)" in r.stdout and "Data after installation: 3 wallet(s)" in r.stdout
    backups = list(node["hdd"].glob("xpubwatch-backup-*"))
    assert len(backups) == 1 and (backups[0] / "xpubwatch.db").exists()
    assert not (node["apps"] / "xpubwatch" / "stale-file").exists()
    assert "systemctl stop xpubwatch" in calls(node) and "manage install xpubwatch" in calls(node)
    node["log"].unlink()
    assert node["vfile"].read_text().strip() == VERSION
    assert run(node, "--no-backup").returncode == 0
    assert "manage reinstall xpubwatch" in calls(node)
    assert len(list(node["hdd"].glob("xpubwatch-backup-*"))) == 1


def test_failed_install_is_reported(node):
    r = run(node, FAIL_INSTALL="1")
    assert r.returncode == 1 and "Installation failed (version file: 'error')" in r.stderr
    assert not any(c.startswith("systemctl restart") for c in calls(node))


def test_service_not_starting_is_reported(node):
    r = run(node, SERVICE_DOWN="1")
    assert r.returncode == 1 and "did not start" in r.stderr


def test_refuses_without_mynode(node, tmp_path):
    shutil.rmtree(node["apps"])
    r = run(node)
    assert r.returncode == 1 and "does not support dynamic apps" in r.stderr


def make_db(folder, wallets):
    import sqlite3

    c = sqlite3.connect(str(folder / "xpubwatch.db"))
    c.execute("CREATE TABLE wallets (id INTEGER PRIMARY KEY, name TEXT)")
    c.executemany("INSERT INTO wallets(name) VALUES (?)", [("w%d" % i,) for i in range(wallets)])
    c.commit()
    c.close()


def test_installed_version_without_database_warns(node):
    """The situation found on a real node: 0.3.9 installed, data folder empty."""
    (node["hdd"] / "xpubwatch").mkdir()
    node["vfile"].write_text("0.3.9\n")
    r = run(node)
    assert r.returncode == 0, r.stderr
    assert "XPUB Watch 0.3.9 is installed, but" in r.stderr and "added again" in r.stderr


def test_lost_wallets_abort(node):
    data = node["hdd"] / "xpubwatch"
    data.mkdir()
    make_db(data, 2)
    node["vfile"].write_text("0.4.0-beta.1\n")
    r = run(node, SHRINK="1")
    assert r.returncode == 1
    assert "now has 0 wallet(s), before the update it had 2" in r.stderr and "xpubwatch-backup-" in r.stderr


def test_database_not_created_is_reported(node):
    r = run(node, NO_DB="1")
    assert r.returncode == 1 and "was not created (none)" in r.stderr


def test_service_heals_data_folder_permissions():
    unit = (APP / "xpubwatch.service").read_text()
    pre = [l for l in unit.splitlines() if l.startswith("ExecStartPre=+")]
    assert pre and "chown -R bitcoin:bitcoin /mnt/hdd/mynode/xpubwatch" in pre[0] and "mkdir -p" in pre[0]
