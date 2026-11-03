import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "mynode-app" / "xpubwatch" / "app_data"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Must be set before the application modules read their configuration.
_TMP = tempfile.mkdtemp(prefix="xpubwatch-tests-")
os.environ["XPUBWATCH_DATA_DIR"] = _TMP
os.environ.setdefault("XPUBWATCH_POLL_SECONDS", "1")
os.environ["XPUBWATCH_START_WATCHER"] = "0"
os.environ["XPUBWATCH_COOKIE_SECURE"] = "0"

from fake_electrum import FakeElectrum  # noqa: E402

from helpers import ZPUB, YPUB, XPUB  # noqa: E402,F401


@pytest.fixture
def db(tmp_path, monkeypatch):
    import db as dbmod

    path = str(tmp_path / "xpubwatch.db")
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod.init_db()
    with dbmod.connect() as conn:
        # One channel is enough to observe every outgoing notification.
        dbmod.set_setting(conn, "ntfy_server", "http://ntfy.invalid")
        dbmod.set_setting(conn, "ntfy_topic", "xpubwatch-test")
    return dbmod


@pytest.fixture
def sent(monkeypatch):
    """Captures notifications as (kind, body) tuples at the HTTP boundary."""
    import notifier

    captured = []

    def fake_post(url, data=b"", headers=None, timeout=8):
        body = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        first = body.split("\n", 1)[0]
        parts = first.split(" ", 2)
        kind = parts[1].rstrip(":") if len(parts) > 1 else ""
        captured.append((kind, body))
        return b"ok"

    monkeypatch.setattr(notifier, "_post", fake_post)
    return captured


@pytest.fixture
def chain():
    return FakeElectrum()


@pytest.fixture
def watcher(db, chain):
    import watcher as w

    return w.Watcher(client=chain)
