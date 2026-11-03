#!/usr/bin/env python3
"""Build the MyNode Community App archive reproducibly.

Usage: scripts/build-release.py [output-dir]   (default: dist/)

Same source tree -> byte-identical archive (fixed mtimes, owners, ordering).
"""
import gzip
import hashlib
import io
import os
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "mynode-app", "xpubwatch")
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log")
EPOCH = 1700000000


def version():
    with open(os.path.join(APP, "app_data", "VERSION"), encoding="utf-8") as f:
        return f.read().strip()


def files():
    for dirpath, dirnames, filenames in os.walk(APP):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        rel_dir = os.path.relpath(dirpath, APP)
        yield rel_dir, None
        for name in sorted(filenames):
            if name.endswith(EXCLUDE_SUFFIXES) or name == ".DS_Store":
                continue
            yield rel_dir, name


def build(out_dir):
    v = version()
    os.makedirs(out_dir, exist_ok=True)
    name = "xpubwatch-mynode-community-app-%s.tar.gz" % v
    path = os.path.join(out_dir, name)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel_dir, fname in files():
            arc_dir = "xpubwatch" if rel_dir == "." else "xpubwatch/" + rel_dir.replace(os.sep, "/")
            if fname is None:
                info = tarfile.TarInfo(arc_dir)
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                data = None
            else:
                src = os.path.join(APP, rel_dir, fname)
                info = tarfile.TarInfo(arc_dir + "/" + fname)
                with open(src, "rb") as f:
                    content = f.read()
                info.size = len(content)
                executable = fname.endswith(".sh") or os.access(src, os.X_OK) and not fname.endswith(".py")
                info.mode = 0o755 if executable else 0o644
                data = io.BytesIO(content)
            info.mtime = EPOCH
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, data)
    with open(path, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=EPOCH) as gz:
            gz.write(raw.getvalue())
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(os.path.join(out_dir, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("%s  %s\n" % (digest, name))
    print(path)
    print("sha256 %s" % digest)
    return path


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist"))
