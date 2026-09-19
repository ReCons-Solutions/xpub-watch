import logging
import os
from flask import Flask, flash, redirect, render_template, request, url_for
from config import WEB_HOST, WEB_PORT
from db import add_wallet, connect, get_setting, init_db, set_setting
from wallet import load_xpub
from watcher import Watcher
from notifier import notify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = Flask(__name__)

secret_path = os.path.join(os.environ.get("XPUBWATCH_DATA_DIR", "/mnt/hdd/mynode/xpubwatch"), "flask_secret")
try:
    with open(secret_path, "r", encoding="utf-8") as f:
        app.secret_key = f.read().strip()
except OSError:
    app.secret_key = os.environ.get("XPUBWATCH_SECRET", os.urandom(32).hex())

init_db()
watcher = Watcher()
watcher.start()


def _derived(conn, wallet_id, branch, limit=5):
    return conn.execute(
        "SELECT branch,idx,address,used FROM addresses WHERE wallet_id=? AND branch=? ORDER BY idx LIMIT ?",
        (wallet_id, branch, limit),
    ).fetchall()


@app.route("/")
def index():
    with connect() as conn:
        wallets = conn.execute("""
            SELECT w.*,
              COALESCE((SELECT SUM(value) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL),0) balance,
              (SELECT COUNT(*) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL) utxo_count,
              (SELECT COUNT(*) FROM addresses a WHERE a.wallet_id=w.id AND a.used=1) used_addresses
            FROM wallets w ORDER BY w.id
        """).fetchall()
        wallet_views = []
        for w in wallets:
            wallet_views.append({"row": w, "receive": _derived(conn, w["id"], 0), "change": _derived(conn, w["id"], 1)})
        events = conn.execute("""
            SELECT e.*, w.name wallet_name FROM events e JOIN wallets w ON w.id=e.wallet_id
            ORDER BY e.id DESC LIMIT 100
        """).fetchall()
        settings = {k: get_setting(conn, k, "") for k in ("ntfy_server","ntfy_topic","ntfy_token","gotify_server","gotify_token","telegram_bot_token","telegram_chat_id","explorer_tx_url")}
        settings.update({
            "notify_receive": get_setting(conn, "notify_receive", "1"),
            "notify_spend": get_setting(conn, "notify_spend", "1"),
            "notify_confirmation": get_setting(conn, "notify_confirmation", "1"),
            "notify_armed_only": get_setting(conn, "notify_armed_only", "1"),
        })
    return render_template(
        "index.html", wallet_views=wallet_views, events=events, settings=settings,
        watcher_error=watcher.last_error, last_scan=watcher.last_scan, tip_height=watcher.tip_height,
        explorer_tx_url=(settings.get("explorer_tx_url") or "https://mempool.space/tx/")
    )


@app.post("/wallets")
def create_wallet():
    name = request.form.get("name", "").strip()
    xpub = request.form.get("xpub", "").strip()
    network = request.form.get("network", "main")
    script_type = request.form.get("script_type", "p2wpkh")
    if network not in ("main", "test") or script_type not in ("p2wpkh", "p2pkh", "p2sh-p2wpkh"):
        flash("Invalid wallet settings", "error")
        return redirect(url_for("index"))
    try:
        load_xpub(xpub)
        if not name:
            raise ValueError("Wallet name is required")
        add_wallet(name, xpub, network, script_type)
        flash("Watch-only wallet added", "ok")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/wallets/<int:wallet_id>/armed")
def set_armed(wallet_id):
    armed = 1 if request.form.get("armed") == "1" else 0
    with connect() as conn:
        conn.execute("UPDATE wallets SET armed=? WHERE id=?", (armed, wallet_id))
    return redirect(url_for("index"))


@app.post("/wallets/<int:wallet_id>/rename")
def rename_wallet(wallet_id):
    name = request.form.get("name", "").strip()
    if not name:
        flash("Wallet name cannot be empty", "error")
    else:
        with connect() as conn:
            conn.execute("UPDATE wallets SET name=? WHERE id=?", (name, wallet_id))
    return redirect(url_for("index"))


@app.post("/wallets/<int:wallet_id>/delete")
def delete_wallet(wallet_id):
    with connect() as conn:
        conn.execute("DELETE FROM events WHERE wallet_id=?", (wallet_id,))
        conn.execute("DELETE FROM utxos WHERE wallet_id=?", (wallet_id,))
        conn.execute("DELETE FROM addresses WHERE wallet_id=?", (wallet_id,))
        conn.execute("DELETE FROM wallets WHERE id=?", (wallet_id,))
    flash("Wallet deleted", "ok")
    return redirect(url_for("index"))


@app.post("/events/<int:event_id>/ack")
def acknowledge(event_id):
    with connect() as conn:
        conn.execute("UPDATE events SET acknowledged=1 WHERE id=?", (event_id,))
    return redirect(url_for("index"))


@app.post("/settings")
def save_settings():
    keys = ("ntfy_server","ntfy_topic","ntfy_token","gotify_server","gotify_token","telegram_bot_token","telegram_chat_id","explorer_tx_url")
    toggle_keys = ("notify_receive", "notify_spend", "notify_confirmation", "notify_armed_only")
    with connect() as conn:
        for key in keys:
            set_setting(conn, key, request.form.get(key, "").strip())
        for key in toggle_keys:
            set_setting(conn, key, "1" if request.form.get(key) == "1" else "0")
    flash("Notification settings saved", "ok")
    return redirect(url_for("index"))


@app.post("/settings/test")
def test_notification():
    notify("Notification test", "info", "test", "Telegram/notification test from XPUB Watch", force=True)
    flash("Test notification sent to all configured notification channels", "ok")
    return redirect(url_for("index"))


@app.get("/health")
def health():
    return {"ok": watcher.last_error is None, "electrum_error": watcher.last_error, "last_scan": watcher.last_scan, "tip_height": watcher.tip_height}


if __name__ == "__main__":
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
