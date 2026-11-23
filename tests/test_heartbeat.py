"""Outage alarm: ping an external monitor after successful scans only."""
import re

import pytest

from helpers import ZPUB


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def pings():
    return []


@pytest.fixture
def hb_watcher(db, chain, pings):
    import watcher as w
    from heartbeat import Heartbeat

    wt = w.Watcher(client=chain)
    clock = Clock()
    wt.heartbeat = Heartbeat(pinger=lambda url: pings.append(url), clock=clock)
    wt.clock = clock
    with db.connect() as conn:
        db.set_setting(conn, "heartbeat_url", "https://hc-ping.example/uuid")
    db.add_wallet("Main", ZPUB, "main", "p2wpkh")
    return wt


def run_once(wt):
    """One iteration of Watcher.run() without sleeping."""
    wt.stop_event.wait = lambda s: wt.stop_event.set()
    wt.stop_event.clear()
    wt.run()


def test_pings_after_successful_scan_and_throttles(db, hb_watcher, pings):
    run_once(hb_watcher)
    assert pings == ["https://hc-ping.example/uuid"]
    run_once(hb_watcher)
    assert len(pings) == 1, "at most one ping per minute"
    hb_watcher.clock.t += 61
    run_once(hb_watcher)
    assert len(pings) == 2
    with db.connect() as conn:
        assert db.get_runtime(conn, "heartbeat_error") is None
        assert int(db.get_runtime(conn, "heartbeat_ok_at")) == int(hb_watcher.clock.t)


def test_no_ping_when_electrum_fails(db, chain, hb_watcher, pings):
    chain.fail_after = 0
    run_once(hb_watcher)
    assert pings == []


def test_ping_error_is_stored_and_never_raises(db, hb_watcher):
    from heartbeat import Heartbeat

    def broken(url):
        raise OSError("connection refused")

    hb_watcher.heartbeat = Heartbeat(pinger=broken, clock=hb_watcher.clock)
    run_once(hb_watcher)
    with db.connect() as conn:
        assert "connection refused" in db.get_runtime(conn, "heartbeat_error")
        assert db.get_runtime(conn, "last_error") is None, "the scan itself succeeded"


def test_no_url_no_ping(db, hb_watcher, pings):
    with db.connect() as conn:
        db.set_setting(conn, "heartbeat_url", "")
    run_once(hb_watcher)
    assert pings == []


def test_settings_ui(db, monkeypatch):
    import app as appmod

    sent = []
    monkeypatch.setattr(appmod, "heartbeat_ping", lambda url: sent.append(url))
    client = appmod.create_app(start_watcher=False).test_client()
    tok = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/setup").get_data(as_text=True)).group(1)
    client.post("/setup", data={"csrf_token": tok, "password": "a long password", "password2": "a long password"})

    def save(**form):
        html = client.get("/").get_data(as_text=True)
        form["csrf_token"] = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
        r = client.post("/settings", data=form, follow_redirects=True)
        assert r.status_code == 200
        return r.get_data(as_text=True)

    html = save(heartbeat_url="javascript:alert(1)")
    assert "must start with https://" in html
    html = save(heartbeat_url="https://hc-ping.example/secret-uuid")
    assert sent == ["https://hc-ping.example/secret-uuid"] and "test ping sent" in html
    assert "secret-uuid" not in client.get("/").get_data(as_text=True), "URL is write-only"
    save(heartbeat_url="")
    with db.connect() as conn:
        assert db.get_setting(conn, "heartbeat_url") == "https://hc-ping.example/secret-uuid", "kept when empty"
    save(clear_heartbeat_url="1")
    with db.connect() as conn:
        assert db.get_setting(conn, "heartbeat_url") == ""


def test_real_ping_checks_status(monkeypatch):
    import io

    import heartbeat

    class Resp(io.BytesIO):
        def __init__(self, code):
            super().__init__(b"OK")
            self.code = code

        def getcode(self):
            return self.code

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen = {}

    def fake(req, timeout=0):
        seen["ua"] = req.get_header("User-agent")
        return Resp(200)

    monkeypatch.setattr(heartbeat.urllib.request, "urlopen", fake)
    assert heartbeat.ping("https://hc-ping.example/x") == 200
    assert seen["ua"].startswith("XPUB-Watch/")
    monkeypatch.setattr(heartbeat.urllib.request, "urlopen", lambda req, timeout=0: Resp(500))
    with pytest.raises(OSError):
        heartbeat.ping("https://hc-ping.example/x")
