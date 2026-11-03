"""Notifications via a transactional outbox.

enqueue() writes the notification in the same database transaction as the
event that caused it. dispatch_pending() sends committed entries afterwards,
tracks delivery per channel and retries failed channels with backoff. A
rollback therefore can never produce an alert for a state that was not
persisted, and a committed state change can never lose its alert.
"""
import json
import logging
import time
import urllib.parse
import urllib.request

from db import connect, get_setting

log = logging.getLogger("xpubwatch.notify")

DEFAULT_EXPLORER_TX_URL = "https://mempool.space/tx/"
MAX_ATTEMPTS = 10
OUTBOX_RETENTION_SECONDS = 30 * 86400
CHANNEL_SETTINGS = {
    "ntfy": ("ntfy_server", "ntfy_topic", "ntfy_token"),
    "telegram": ("telegram_bot_token", "telegram_chat_id"),
    "gotify": ("gotify_server", "gotify_token"),
}
KIND_LABELS = {
    "receive": "RECEIVE",
    "receive_dropped": "RECEIVE DROPPED",
    "spend": "SPEND",
    "spend_replaced": "SPEND REPLACED",
    "spend_reverted": "SPEND REVERTED",
    "spend_confirmed": "SPEND CONFIRMED",
    "test": "TEST",
}
KIND_TOGGLES = {
    "receive": "notify_receive",
    "receive_dropped": "notify_receive",
    "spend": "notify_spend",
    "spend_replaced": "notify_spend",
    "spend_reverted": "notify_spend",
    "spend_confirmed": "notify_confirmation",
}


def _post(url, data=b"", headers=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _setting_enabled(conn, key, default=True):
    value = get_setting(conn, key, "1" if default else "0").strip().lower()
    return value in ("1", "true", "yes", "on")


def notification_allowed(conn, kind, wallet_armed):
    if kind == "test":
        return True
    if _setting_enabled(conn, "notify_armed_only", True) and not wallet_armed:
        return False
    toggle = KIND_TOGGLES.get(kind)
    return _setting_enabled(conn, toggle, True) if toggle else True


def explorer_url(base, txid):
    if not txid:
        return None
    base = (base or "").strip() or DEFAULT_EXPLORER_TX_URL
    if "{txid}" in base:
        return base.replace("{txid}", urllib.parse.quote(txid, safe=""))
    return base.rstrip("/") + "/" + urllib.parse.quote(txid, safe="")


def _status_text(status, confirmations):
    status = (status or "unknown").lower()
    confs = int(confirmations or 0)
    if status == "mempool":
        return "MEMPOOL / unconfirmed"
    if status == "confirmed":
        return "CONFIRMED (%d confirmation%s)" % (confs, "" if confs == 1 else "s")
    if status == "detected":
        return "DETECTED / spending TX not resolved yet"
    return status.upper()


def _amount_text(amount_sats):
    if amount_sats is None:
        return None
    sats = int(amount_sats)
    return "%s sat (%.8f BTC)" % (format(sats, ","), sats / 100000000.0)


def format_plain(n, link):
    body = "%s %s: %s" % (n["severity"].upper(), n["kind"], n["message"])
    amount = _amount_text(n.get("amount_sats"))
    if amount:
        body += "\nAmount: %s" % amount
    if n.get("status"):
        body += "\nStatus: %s" % _status_text(n["status"], n.get("confirmations"))
    elif n["kind"] != "test":
        body += "\nStatus: UNKNOWN"
    if n.get("txid"):
        body += "\nTXID: %s" % n["txid"]
        if link:
            body += "\nTransaction: %s" % link
    return body


def format_telegram(n, link):
    kind = n["kind"]
    if n["severity"] == "critical":
        icon = "🚨"
    elif n["severity"] == "warning":
        icon = "⚠️"
    else:
        icon = "ℹ️"
    lines = ["%s XPUB Watch — %s" % (icon, KIND_LABELS.get(kind, kind.upper())), "", "Wallet: %s" % n["wallet_name"]]
    amount = _amount_text(n.get("amount_sats"))
    if amount:
        lines.append("Amount: %s" % amount)
    if kind == "test" or kind in ("receive_dropped", "spend_reverted", "spend_replaced"):
        lines.append(n["message"])
    if n.get("status"):
        lines.append("Status: %s" % _status_text(n["status"], n.get("confirmations")))
    elif kind != "test":
        lines.append("Status: UNKNOWN")
    if kind != "test" and n.get("status") != "confirmed":
        lines.append("Confirmations: %d" % int(n.get("confirmations") or 0))
    if n.get("txid"):
        lines.append("TXID: %s" % n["txid"])
        if link:
            lines.append("Transaction: %s" % link)
    return "\n".join(lines)


def _channel_config(conn):
    cfg = {key: get_setting(conn, key, "").strip() for keys in CHANNEL_SETTINGS.values() for key in keys}
    cfg["explorer_tx_url"] = get_setting(conn, "explorer_tx_url", DEFAULT_EXPLORER_TX_URL)
    return cfg


def configured_channels(cfg):
    channels = []
    if cfg.get("ntfy_server") and cfg.get("ntfy_topic"):
        channels.append("ntfy")
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        channels.append("telegram")
    if cfg.get("gotify_server") and cfg.get("gotify_token"):
        channels.append("gotify")
    return channels


def _send(channel, n, cfg):
    link = explorer_url(cfg.get("explorer_tx_url"), n.get("txid"))
    title = "XPUB Watch: %s" % n["wallet_name"]
    if channel == "ntfy":
        url = cfg["ntfy_server"].rstrip("/") + "/" + urllib.parse.quote(cfg["ntfy_topic"], safe="")
        headers = {"Title": title, "Priority": "urgent" if n["severity"] == "critical" else "default"}
        if cfg.get("ntfy_token"):
            headers["Authorization"] = "Bearer " + cfg["ntfy_token"]
        _post(url, format_plain(n, link).encode("utf-8"), headers)
    elif channel == "telegram":
        url = "https://api.telegram.org/bot" + urllib.parse.quote(cfg["telegram_bot_token"], safe=":") + "/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": cfg["telegram_chat_id"],
            "text": format_telegram(n, link),
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        _post(url, payload, {"Content-Type": "application/x-www-form-urlencoded"})
    elif channel == "gotify":
        url = cfg["gotify_server"].rstrip("/") + "/message"
        payload = json.dumps({
            "title": title,
            "message": format_plain(n, link),
            "priority": 10 if n["severity"] == "critical" else 5,
        }).encode("utf-8")
        _post(url, payload, {"Content-Type": "application/json", "X-Gotify-Key": cfg["gotify_token"]})
    else:
        raise ValueError("unknown channel %s" % channel)


def enqueue(conn, wallet, severity, kind, message, txid=None, status=None, confirmations=0, amount_sats=None):
    """Queue a notification inside the caller's transaction.

    Returns False when the user's notification settings suppress it.
    """
    if not notification_allowed(conn, kind, bool(wallet["armed"])):
        log.info("notification suppressed: wallet=%s kind=%s armed=%s", wallet["name"], kind, bool(wallet["armed"]))
        return False
    conn.execute(
        "INSERT INTO outbox(created_at,wallet_id,wallet_name,severity,kind,message,txid,status,confirmations,amount_sats)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (int(time.time()), wallet["id"], wallet["name"], severity, kind, message, txid, status,
         int(confirmations or 0), amount_sats),
    )
    return True


def _backoff(attempts):
    return min(3600, 30 * (2 ** max(0, attempts - 1)))


def dispatch_pending(now=None, limit=50):
    """Send committed outbox entries. Safe to call often; never raises."""
    now = int(now or time.time())
    try:
        with connect() as conn:
            conn.execute("DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at < ?",
                         (now - OUTBOX_RETENTION_SECONDS,))
            cfg = _channel_config(conn)
            rows = conn.execute(
                "SELECT * FROM outbox WHERE sent_at IS NULL AND attempts < ? AND next_attempt_at <= ?"
                " ORDER BY id LIMIT ?",
                (MAX_ATTEMPTS, now, limit),
            ).fetchall()
    except Exception:
        log.exception("reading notification outbox failed")
        return 0

    channels = configured_channels(cfg)
    sent = 0
    for row in rows:
        n = dict(row)
        delivered = set(filter(None, (n["delivered"] or "").split(",")))
        errors = []
        for channel in channels:
            if channel in delivered:
                continue
            try:
                _send(channel, n, cfg)
                delivered.add(channel)
            except Exception as exc:
                errors.append("%s: %s" % (channel, exc))
                log.warning("%s notification failed: %s", channel, exc)
        attempts = n["attempts"] + 1
        done = not errors or attempts >= MAX_ATTEMPTS
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE outbox SET attempts=?, delivered=?, last_error=?, next_attempt_at=?, sent_at=? WHERE id=?",
                    (attempts, ",".join(sorted(delivered)), "; ".join(errors) or None,
                     now + _backoff(attempts), now if done else None, n["id"]),
                )
        except Exception:
            log.exception("updating notification outbox failed")
        if not errors:
            sent += 1
    return sent


def send_test(wallet_name="Notification test"):
    """Send a test message synchronously. Returns {channel: None | error text}."""
    with connect() as conn:
        cfg = _channel_config(conn)
    n = {
        "wallet_name": wallet_name, "severity": "info", "kind": "test",
        "message": "Notification test from XPUB Watch", "txid": None,
        "status": None, "confirmations": 0, "amount_sats": None,
    }
    results = {}
    for channel in configured_channels(cfg):
        try:
            _send(channel, n, cfg)
            results[channel] = None
        except Exception as exc:
            results[channel] = str(exc)
    return results
