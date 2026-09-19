import os
import sqlite3
import time
from contextlib import contextmanager
from config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    xpub TEXT NOT NULL,
    network TEXT NOT NULL DEFAULT 'main',
    script_type TEXT NOT NULL DEFAULT 'p2wpkh',
    armed INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS addresses (
    wallet_id INTEGER NOT NULL,
    branch INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    address TEXT NOT NULL,
    scripthash TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(wallet_id, branch, idx)
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
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def _columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}


def _migrate(conn):
    ucols = _columns(conn, "utxos")
    if "spend_txid" not in ucols:
        conn.execute("ALTER TABLE utxos ADD COLUMN spend_txid TEXT")
    if "spend_height" not in ucols:
        conn.execute("ALTER TABLE utxos ADD COLUMN spend_height INTEGER")
    if "spend_confirm_notified" not in ucols:
        conn.execute("ALTER TABLE utxos ADD COLUMN spend_confirm_notified INTEGER NOT NULL DEFAULT 0")
    ecols = _columns(conn, "events")
    if "status" not in ecols:
        conn.execute("ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'unknown'")
    if "confirmations" not in ecols:
        conn.execute("ALTER TABLE events ADD COLUMN confirmations INTEGER NOT NULL DEFAULT 0")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_wallet(name, xpub, network="main", script_type="p2wpkh"):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO wallets(name,xpub,network,script_type,armed,created_at) VALUES(?,?,?,?,1,?)",
            (name, xpub, network, script_type, int(time.time())),
        )
        return cur.lastrowid


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
