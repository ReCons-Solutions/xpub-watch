import os
import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    xpub TEXT NOT NULL,
    network TEXT NOT NULL DEFAULT 'main',
    script_type TEXT NOT NULL DEFAULT 'p2wpkh',
    armed INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS addresses (
    wallet_id INTEGER NOT NULL,
    branch INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    address TEXT NOT NULL,
    scripthash TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    PRIMARY KEY(wallet_id, branch, idx)
);
CREATE INDEX IF NOT EXISTS addresses_scripthash ON addresses(wallet_id, scripthash);
CREATE TABLE IF NOT EXISTS addr_txs (
    wallet_id INTEGER NOT NULL,
    scripthash TEXT NOT NULL,
    txid TEXT NOT NULL,
    height INTEGER NOT NULL,
    PRIMARY KEY(wallet_id, scripthash, txid)
);
CREATE TABLE IF NOT EXISTS tx_cache (
    txid TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS utxos (
    wallet_id INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    tx_pos INTEGER NOT NULL,
    value INTEGER NOT NULL,
    height INTEGER NOT NULL,
    scripthash TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    spent_at INTEGER,
    spend_txid TEXT,
    spend_height INTEGER,
    spend_confirm_notified INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(wallet_id, tx_hash, tx_pos)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    severity TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    txid TEXT,
    status TEXT NOT NULL DEFAULT 'unknown',
    confirmations INTEGER NOT NULL DEFAULT 0,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    wallet_id INTEGER,
    wallet_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    txid TEXT,
    status TEXT,
    confirmations INTEGER NOT NULL DEFAULT 0,
    amount_sats INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    delivered TEXT NOT NULL DEFAULT '',
    sent_at INTEGER,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS runtime (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def _add_column(conn, table, column, ddl):
    if column not in _columns(conn, table):
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))


def _migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    # 0.3.x / 0.4.0 databases.
    _add_column(conn, "utxos", "spend_txid", "TEXT")
    _add_column(conn, "utxos", "spend_height", "INTEGER")
    _add_column(conn, "utxos", "spend_confirm_notified", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "events", "status", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column(conn, "events", "confirmations", "INTEGER NOT NULL DEFAULT 0")
    if version < 2:
        _add_column(conn, "wallets", "synced", "INTEGER NOT NULL DEFAULT 0")
        _add_column(conn, "addresses", "status", "TEXT")
        # Existing wallets are re-baselined once by the transaction ledger:
        # outputs unknown to the old UTXO table are recorded silently, while
        # state changes of already known UTXOs still raise alerts.
        conn.execute("UPDATE wallets SET synced=0")
        conn.execute("UPDATE addresses SET status=NULL")
    conn.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


@contextmanager
def connect():
    """One connection = one transaction. Commits on success, rolls back on error."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_wallet(name, xpub, network="main", script_type="p2wpkh"):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO wallets(name,xpub,network,script_type,armed,created_at,synced) VALUES(?,?,?,?,1,?,0)",
            (name, xpub, network, script_type, int(time.time())),
        )
        return cur.lastrowid


def delete_wallet(wallet_id):
    with connect() as conn:
        for table in ("events", "utxos", "addresses", "addr_txs", "outbox"):
            conn.execute("DELETE FROM %s WHERE wallet_id=?" % table, (wallet_id,))
        conn.execute("DELETE FROM wallets WHERE id=?", (wallet_id,))
        # Drop cached transactions no remaining wallet refers to (privacy + size).
        conn.execute("DELETE FROM tx_cache WHERE txid NOT IN (SELECT txid FROM addr_txs)")


def event(conn, wallet_id, severity, kind, message, txid=None, status="unknown", confirmations=0):
    cur = conn.execute(
        "INSERT INTO events(wallet_id,created_at,severity,kind,message,txid,status,confirmations) VALUES(?,?,?,?,?,?,?,?)",
        (wallet_id, int(time.time()), severity, kind, message, txid, status, int(confirmations or 0)),
    )
    return cur.lastrowid


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value or ""),
    )


def get_runtime(conn, key, default=None):
    row = conn.execute("SELECT value FROM runtime WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_runtime(conn, key, value):
    conn.execute(
        "INSERT INTO runtime(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)),
    )
