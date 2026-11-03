"""Outbox delivery, settings gating, reorgs and migration of 0.4 databases."""
import sqlite3
import time

from helpers import ZPUB, events, kinds, spk


def add(dbmod, xpub=ZPUB, script_type="p2wpkh", name="Main"):
    return dbmod.add_wallet(name, xpub, "main", script_type)


def test_spend_first_seen_confirmed_alerts_once(db, chain, watcher, sent):
    add(db)
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 10_000, height=chain.tip_height)
    watcher.scan_once()
    spend = chain.spend([op])
    chain.confirm(spend)
    watcher.scan_once()
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend"]
    assert events(db, "spend")[-1]["status"] == "confirmed"


def test_reorg_of_spend_confirmation_rearms_alert(db, chain, watcher, sent):
    add(db)
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 10_000, height=chain.tip_height)
    watcher.scan_once()
    spend = chain.spend([op])
    watcher.scan_once()
    chain.confirm(spend)
    watcher.scan_once()
    chain.txs[spend] = (chain.txs[spend][0], 0)  # block reorganised away
    watcher.scan_once()
    chain.confirm(spend)
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend", "spend_confirmed", "spend_confirmed"]


def test_notification_toggles(db, chain, watcher, sent):
    add(db)
    with db.connect() as conn:
        db.set_setting(conn, "notify_receive", "0")
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 10_000, height=chain.tip_height)
    watcher.scan_once()
    chain.spend([op])
    watcher.scan_once()
    assert kinds(sent) == ["spend"]
    assert [e["kind"] for e in events(db) if e["kind"] != "sync"] == ["receive", "spend"]


def test_failed_channel_is_retried_without_duplicating_others(db, chain, watcher, monkeypatch):
    import notifier

    with db.connect() as conn:
        db.set_setting(conn, "gotify_server", "http://gotify.invalid")
        db.set_setting(conn, "gotify_token", "tok")
    calls = []
    state = {"gotify_down": True}

    def fake_post(url, data=b"", headers=None, timeout=8):
        channel = "gotify" if "gotify" in url else "ntfy"
        if channel == "gotify" and state["gotify_down"]:
            raise OSError("connection refused")
        calls.append(channel)
        return b"ok"

    monkeypatch.setattr(notifier, "_post", fake_post)
    add(db)
    watcher.scan_once()
    chain.pay(spk(ZPUB, 0), 10_000)
    watcher.scan_once()
    assert calls == ["ntfy"]
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM outbox").fetchone()
        assert row["sent_at"] is None and row["delivered"] == "ntfy" and "gotify" in row["last_error"]

    state["gotify_down"] = False
    notifier.dispatch_pending(now=time.time() + 3600)
    assert calls == ["ntfy", "gotify"]
    with db.connect() as conn:
        assert conn.execute("SELECT sent_at FROM outbox").fetchone()[0] is not None
    notifier.dispatch_pending(now=time.time() + 7200)
    assert calls == ["ntfy", "gotify"]


def test_gotify_token_not_in_url(db, chain, watcher, monkeypatch):
    import notifier

    with db.connect() as conn:
        db.set_setting(conn, "ntfy_server", "")
        db.set_setting(conn, "gotify_server", "http://gotify.invalid")
        db.set_setting(conn, "gotify_token", "SECRET")
    seen = []
    monkeypatch.setattr(notifier, "_post", lambda url, data=b"", headers=None, timeout=8: seen.append((url, headers)))
    notifier.send_test()
    url, headers = seen[0]
    assert "SECRET" not in url and headers["X-Gotify-Key"] == "SECRET"


def test_migration_from_04_database(tmp_path, monkeypatch, chain, sent):
    """A 0.4 database is upgraded; unknown history is recorded silently, but
    a spend of a UTXO the old version already tracked still raises an alarm."""
    import db as dbmod
    import notifier
    import watcher as w
    from fake_electrum import scripthash_of

    path = str(tmp_path / "old.db")
    # Chain: an old (already spent before) output, plus a tracked UTXO that is spent now.
    old_op = chain.pay(spk(ZPUB, 0), 5_000, height=chain.tip_height - 100)
    chain.spend([old_op], height=chain.tip_height - 90)
    tracked = chain.pay(spk(ZPUB, 1), 70_000, height=chain.tip_height - 50)
    spend = chain.spend([tracked])

    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE wallets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, xpub TEXT NOT NULL,
        network TEXT NOT NULL DEFAULT 'main', script_type TEXT NOT NULL DEFAULT 'p2wpkh',
        armed INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
    CREATE TABLE addresses (wallet_id INTEGER NOT NULL, branch INTEGER NOT NULL, idx INTEGER NOT NULL,
        address TEXT NOT NULL, scripthash TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(wallet_id, branch, idx));
    CREATE TABLE utxos (wallet_id INTEGER NOT NULL, tx_hash TEXT NOT NULL, tx_pos INTEGER NOT NULL,
        value INTEGER NOT NULL, height INTEGER NOT NULL, scripthash TEXT NOT NULL, first_seen INTEGER NOT NULL,
        spent_at INTEGER, spend_txid TEXT, spend_height INTEGER,
        spend_confirm_notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(wallet_id, tx_hash, tx_pos));
    CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, wallet_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL, severity TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
        txid TEXT, status TEXT NOT NULL DEFAULT 'unknown', confirmations INTEGER NOT NULL DEFAULT 0,
        acknowledged INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
    """)
    conn.execute("INSERT INTO wallets(name,xpub,created_at) VALUES('Legacy',?,0)", (ZPUB,))
    conn.execute("INSERT INTO utxos VALUES(1,?,?,70000,?,?,0,NULL,NULL,NULL,0)",
                 (tracked[0], tracked[1], chain.tip_height - 50, scripthash_of(spk(ZPUB, 1))))
    conn.execute("INSERT INTO events(wallet_id,created_at,severity,kind,message,txid) VALUES(1,0,'info','receive','old',?)",
                 (tracked[0],))
    conn.execute("INSERT INTO settings VALUES('ntfy_server','http://ntfy.invalid'),('ntfy_topic','t')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", path)
    dbmod.init_db()
    w.Watcher(client=chain).scan_once()

    assert kinds(sent) == ["spend"]
    assert sent[0][1].count(spend) >= 1
    with dbmod.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
        assert c.execute("SELECT synced FROM wallets").fetchone()[0] == 1
        assert c.execute("SELECT confirmations FROM events WHERE kind='receive'").fetchone()[0] == 51
    notifier.dispatch_pending()
    assert kinds(sent) == ["spend"]
