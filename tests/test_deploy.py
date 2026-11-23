"""scripts/deploy-to-mynode.sh with stubbed ssh, scp and sudo (node = local fake home)."""
import hashlib
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy-to-mynode.sh"
NAME = "xpubwatch-mynode-community-app-9.9.9.tar.gz"


@pytest.fixture
def env(tmp_path):
    node_home = tmp_path / "node-home"
    node_home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    # ssh stub: drops options and target, runs the remote command in the fake node home.
    (bin_dir / "ssh").write_text(f"""#!/bin/bash
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-O) shift 2 ;;
    -t) shift ;;
    *) args+=("$1"); shift ;;
  esac
done
echo "ssh ${{args[*]}}" >> {log}
[ ${{#args[@]}} -le 1 ] && exit 0
cd {node_home} && HOME={node_home} PATH={bin_dir}:$PATH bash -c "${{args[1]}}"
""")
    (bin_dir / "scp").write_text(f"""#!/bin/bash
src="${{@: -2:1}}"; dst="${{@: -1}}"
echo "scp $src $dst" >> {log}
cp "$src" {node_home}/"${{dst#*:}}"
""")
    (bin_dir / "sudo").write_text(f"""#!/bin/bash
echo "sudo $*" >> {log}
test -f xpubwatch/install-on-mynode.sh || exit 9
exit ${{INSTALL_EXIT:-0}}
""")
    for f in bin_dir.iterdir():
        f.chmod(0o755)

    work = tmp_path / "download"
    work.mkdir()
    src = tmp_path / "src" / "xpubwatch"
    src.mkdir(parents=True)
    (src / "install-on-mynode.sh").write_text("echo installer\n")
    archive = work / NAME
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src, arcname="xpubwatch")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (work / "SHA256SUMS").write_text(f"{digest}  {NAME}\n")

    e = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    e.pop("MYNODE_HOST", None)

    def run(*args, **extra):
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=work, env=dict(e, **extra),
                              capture_output=True, text=True)

    return run, log, node_home, work


def test_copies_and_installs_newest_archive(env):
    run, log, node_home, _ = env
    r = run()
    assert r.returncode == 0, r.stderr
    assert "Checksum OK" in r.stdout
    calls = log.read_text()
    assert "ssh admin@mynode.local" in calls
    assert f"scp ./{NAME} admin@mynode.local:xpubwatch-install/{NAME}" in calls
    assert "sudo bash xpubwatch/install-on-mynode.sh\n" in calls
    assert not (node_home / "xpubwatch-install").exists(), "cleaned up after success"


def test_target_and_installer_args(env):
    run, log, _, _ = env
    r = run(NAME, "admin@192.168.1.20", "--no-backup")
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "admin@192.168.1.20" in calls and "sudo bash xpubwatch/install-on-mynode.sh --no-backup" in calls


def test_checksum_mismatch_aborts_before_copy(env):
    run, log, _, work = env
    (work / "SHA256SUMS").write_text("0" * 64 + f"  {NAME}\n")
    r = run()
    assert r.returncode != 0 and "Checksum mismatch" in r.stderr
    assert not log.exists() or "scp" not in log.read_text()


def test_failed_installer_keeps_files(env):
    run, _, node_home, _ = env
    r = run(INSTALL_EXIT="4")
    assert r.returncode == 4 and "installer failed" in r.stderr
    assert (node_home / "xpubwatch-install" / NAME).exists()


def test_rejects_unsafe_installer_argument(env):
    run, log, _, _ = env
    r = run(NAME, "admin@mynode.local", "x;reboot")
    assert r.returncode != 0 and "Unsupported installer argument" in r.stderr
    assert not log.exists(), "nothing sent to the node"
