import fcntl
import logging
import os
import secrets
import time
from datetime import timedelta

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import auth
from config import (COOKIE_SECURE, DATA_DIR, LOOKAHEAD, POLL_SECONDS, SESSION_HOURS, START_WATCHER,
                    VERSION, WEB_HOST, WEB_PORT)
from db import add_wallet, connect, delete_wallet, get_runtime, get_setting, init_db, set_setting
from notifier import explorer_url, send_test
from wallet import SCRIPT_TYPES, address_for, inspect_key

log = logging.getLogger("xpubwatch.web")

URL_SETTINGS = ("ntfy_server", "gotify_server", "explorer_tx_url")
SECRET_SETTINGS = ("ntfy_token", "gotify_token", "telegram_bot_token")
PLAIN_SETTINGS = ("ntfy_topic", "telegram_chat_id")
TOGGLE_SETTINGS = ("notify_receive", "notify_spend", "notify_confirmation", "notify_armed_only")
PUBLIC_ENDPOINTS = {"login", "setup", "health", "static"}
SCRIPT_LABELS = {
    "p2wpkh": "Native SegWit (BIP84, bc1q…)",
    "p2sh-p2wpkh": "Nested SegWit (BIP49, 3…)",
    "p2pkh": "Legacy (BIP44, 1…)",
}
MAX_NAME = 80

_watcher = None
_watcher_lock_file = None


def _load_secret_key():
    path = os.path.join(DATA_DIR, "flask_secret")
    try:
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key + "\n")
    except FileExistsError:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        log.warning("could not persist session secret; sessions end on restart")
    return key


def start_watcher_once():
    """Start the background watcher in exactly one process (file lock)."""
    global _watcher, _watcher_lock_file
    if _watcher is not None:
        return _watcher
    os.makedirs(DATA_DIR, exist_ok=True)
    lock = open(os.path.join(DATA_DIR, "watcher.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        log.info("watcher already running in another process")
        return None
    from watcher import Watcher

    _watcher_lock_file = lock
    _watcher = Watcher()
    _watcher.start()
    return _watcher


def _fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except (TypeError, ValueError):
        return ""


def _btc(sats):
    return "%.8f" % (int(sats or 0) / 100000000.0)


def _valid_url(value):
    return value == "" or value.startswith("https://") or value.startswith("http://")


def _watcher_state():
    with connect() as conn:
        state = {k: get_runtime(conn, k) for k in ("last_scan", "last_error", "tip_height", "heartbeat")}
    heartbeat = int(state["heartbeat"] or 0)
    state["stale"] = heartbeat == 0 or time.time() - heartbeat > max(120, POLL_SECONDS * 6)
    state["ok"] = not state["last_error"] and not state["stale"]
    return state


def create_app(start_watcher=START_WATCHER):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_load_secret_key(),
        SESSION_COOKIE_NAME="xpubwatch_session",  # MyNode's own UI uses "session" on the same host
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=COOKIE_SECURE,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.jinja_env.filters["ts"] = _fmt_time
    app.jinja_env.filters["btc"] = _btc
    init_db()

    @app.context_processor
    def inject():
        return {"csrf_token": auth.csrf_token, "version": VERSION, "script_labels": SCRIPT_LABELS}

    @app.before_request
    def guard():
        if request.method == "POST":
            auth.check_csrf()
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if not auth.password_configured():
            return redirect(url_for("setup"))
        if not auth.logged_in():
            return redirect(url_for("login"))
        return None

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ auth pages
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if auth.password_configured():
            return redirect(url_for("index"))
        if request.method == "POST":
            password = request.form.get("password", "")
            if password != request.form.get("password2", ""):
                flash("Passwords do not match", "error")
            else:
                try:
                    auth.set_password(password)
                    auth.log_in()
                    flash("Password set. Keep it in your password manager.", "ok")
                    return redirect(url_for("index"))
                except ValueError as exc:
                    flash(str(exc), "error")
        return render_template("setup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not auth.password_configured():
            return redirect(url_for("setup"))
        if request.method == "POST":
            key = auth.client_key()
            wait = auth.throttle.retry_after(key)
            if wait:
                flash("Too many failed attempts. Try again in %d seconds." % wait, "error")
                return render_template("login.html"), 429
            if auth.verify_password(request.form.get("password", "")):
                auth.throttle.success(key)
                auth.log_in()
                return redirect(url_for("index"))
            auth.throttle.failure(key)
            log.warning("failed login from %s", key)
            flash("Wrong password", "error")
            return render_template("login.html"), 401
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.post("/password")
    def change_password():
        if not auth.verify_password(request.form.get("current_password", "")):
            flash("Current password is wrong", "error")
        elif request.form.get("new_password", "") != request.form.get("new_password2", ""):
            flash("New passwords do not match", "error")
        else:
            try:
                auth.set_password(request.form.get("new_password", ""))
                auth.log_in()
                flash("Password changed", "ok")
            except ValueError as exc:
                flash(str(exc), "error")
        return redirect(url_for("index") + "#security")

    # ------------------------------------------------------------- dashboard
    @app.route("/")
    def index():
        with connect() as conn:
            wallets = conn.execute("""
                SELECT w.*,
                  COALESCE((SELECT SUM(value) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL),0) balance,
                  COALESCE((SELECT SUM(value) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL
                            AND u.height<=0),0) unconfirmed,
                  (SELECT COUNT(*) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL) utxo_count,
                  (SELECT COUNT(*) FROM addresses a WHERE a.wallet_id=w.id AND a.used=1) used_addresses
                FROM wallets w ORDER BY w.id
            """).fetchall()
            wallet_views = []
            for w in wallets:
                addrs = {}
                for branch in (0, 1):
                    addrs[branch] = conn.execute(
                        "SELECT idx,address,used FROM addresses WHERE wallet_id=? AND branch=? ORDER BY idx LIMIT 5",
                        (w["id"], branch),
                    ).fetchall()
                wallet_views.append({"row": w, "receive": addrs[0], "change": addrs[1]})
            events = conn.execute("""
                SELECT e.*, w.name wallet_name FROM events e JOIN wallets w ON w.id=e.wallet_id
                ORDER BY e.id DESC LIMIT 100
            """).fetchall()
            unacked = conn.execute("SELECT COUNT(*) FROM events WHERE acknowledged=0").fetchone()[0]
            settings = {k: get_setting(conn, k, "") for k in URL_SETTINGS + PLAIN_SETTINGS}
            secrets_set = {k: bool(get_setting(conn, k, "")) for k in SECRET_SETTINGS}
            for k in TOGGLE_SETTINGS:
                settings[k] = get_setting(conn, k, "1")
            pending = conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT last_error FROM outbox WHERE last_error IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        explorer = settings.get("explorer_tx_url") or ""
        return render_template(
            "index.html", wallet_views=wallet_views, events=events, unacked=unacked,
            settings=settings, secrets_set=secrets_set, state=_watcher_state(),
            outbox_pending=pending, outbox_error=failed["last_error"] if failed else None,
            tx_link=lambda txid: explorer_url(explorer, txid), lookahead=LOOKAHEAD,
        )

    def _wallet_or_404(conn, wallet_id):
        row = conn.execute("SELECT * FROM wallets WHERE id=?", (wallet_id,)).fetchone()
        if row is None:
            abort(404)
        return row

    @app.post("/wallets")
    def create_wallet():
        name = request.form.get("name", "").strip()
        script_type = request.form.get("script_type", "auto")
        try:
            if not name:
                raise ValueError("Wallet name is required")
            if len(name) > MAX_NAME:
                raise ValueError("Wallet name is too long (max %d characters)" % MAX_NAME)
            info = inspect_key(request.form.get("xpub", ""))
            if script_type == "auto":
                if info.implied_script_type is None:
                    raise ValueError(
                        "An xpub/tpub does not encode the address type. Select the script type explicitly "
                        "(check the first receive address against your wallet afterwards)."
                    )
                script_type = info.implied_script_type
            elif script_type not in SCRIPT_TYPES:
                raise ValueError("Invalid script type")
            elif info.implied_script_type and info.implied_script_type != script_type:
                raise ValueError(
                    "This key prefix implies %s, but %s was selected"
                    % (SCRIPT_LABELS[info.implied_script_type], SCRIPT_LABELS[script_type])
                )
            with connect() as conn:
                for w in conn.execute("SELECT name, xpub, script_type FROM wallets"):
                    try:
                        same = inspect_key(w["xpub"]).normalized == info.normalized
                    except ValueError:
                        same = False
                    if same and w["script_type"] == script_type:
                        raise ValueError("This key is already watched as \"%s\"" % w["name"])
            add_wallet(name, request.form.get("xpub", "").strip(), info.network, script_type)
            first = address_for(info.normalized, 0, 0, info.network, script_type)
            flash("Watch-only wallet added. Verify that the first receive address in your wallet is %s" % first, "ok")
            if info.depth != 3:
                flash("Note: this key has depth %d, account keys usually have depth 3. Verify the derived "
                      "addresses before relying on alerts." % info.depth, "error")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("index"))

    @app.post("/wallets/<int:wallet_id>/armed")
    def set_armed(wallet_id):
        armed = 1 if request.form.get("armed") == "1" else 0
        with connect() as conn:
            _wallet_or_404(conn, wallet_id)
            conn.execute("UPDATE wallets SET armed=? WHERE id=?", (armed, wallet_id))
        return redirect(url_for("index"))

    @app.post("/wallets/<int:wallet_id>/rename")
    def rename_wallet(wallet_id):
        name = request.form.get("name", "").strip()
        if not name or len(name) > MAX_NAME:
            flash("Wallet name must have 1–%d characters" % MAX_NAME, "error")
        else:
            with connect() as conn:
                _wallet_or_404(conn, wallet_id)
                conn.execute("UPDATE wallets SET name=? WHERE id=?", (name, wallet_id))
        return redirect(url_for("index"))

    @app.post("/wallets/<int:wallet_id>/delete")
    def remove_wallet(wallet_id):
        with connect() as conn:
            _wallet_or_404(conn, wallet_id)
        delete_wallet(wallet_id)
        flash("Wallet deleted", "ok")
        return redirect(url_for("index"))

    @app.post("/events/<int:event_id>/ack")
    def acknowledge(event_id):
        with connect() as conn:
            conn.execute("UPDATE events SET acknowledged=1 WHERE id=?", (event_id,))
        return redirect(url_for("index") + "#events")

    @app.post("/events/ack-all")
    def acknowledge_all():
        with connect() as conn:
            conn.execute("UPDATE events SET acknowledged=1 WHERE acknowledged=0")
        return redirect(url_for("index") + "#events")

    @app.post("/settings")
    def save_settings():
        values = {k: request.form.get(k, "").strip() for k in URL_SETTINGS + PLAIN_SETTINGS}
        bad = [k for k in URL_SETTINGS if not _valid_url(values[k])]
        if bad:
            flash("URLs must start with https:// or http:// (%s)" % ", ".join(bad), "error")
            return redirect(url_for("index") + "#notifications")
        with connect() as conn:
            for k, v in values.items():
                set_setting(conn, k, v)
            for k in SECRET_SETTINGS:
                new = request.form.get(k, "").strip()
                if request.form.get("clear_" + k) == "1":
                    set_setting(conn, k, "")
                elif new:
                    set_setting(conn, k, new)
            for k in TOGGLE_SETTINGS:
                set_setting(conn, k, "1" if request.form.get(k) == "1" else "0")
        flash("Notification settings saved", "ok")
        return redirect(url_for("index") + "#notifications")

    @app.post("/settings/test")
    def test_notification():
        results = send_test()
        if not results:
            flash("No notification channel configured", "error")
        for channel, error in results.items():
            flash("Test via %s: %s" % (channel, error or "sent"), "error" if error else "ok")
        return redirect(url_for("index") + "#notifications")

    @app.get("/health")
    def health():
        state = _watcher_state()
        body = {"ok": state["ok"]}
        if auth.logged_in():
            body.update({k: state[k] for k in ("last_scan", "last_error", "tip_height", "stale")})
        return body, 200 if state["ok"] else 503

    if start_watcher:
        start_watcher_once()
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
