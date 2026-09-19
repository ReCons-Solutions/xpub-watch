import json
import logging
import urllib.parse
import urllib.request
from db import connect, get_setting

log = logging.getLogger("xpubwatch.notify")
DEFAULT_EXPLORER_TX_URL = "https://mempool.space/tx/"


def _post(url, data=b"", headers=None, timeout=8):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _explorer_url(txid):
    if not txid:
        return None
    with connect() as conn:
        base = get_setting(conn, "explorer_tx_url", DEFAULT_EXPLORER_TX_URL).strip() or DEFAULT_EXPLORER_TX_URL
    if "{txid}" in base:
        return base.replace("{txid}", urllib.parse.quote(txid, safe=""))
    return base.rstrip("/") + "/" + urllib.parse.quote(txid, safe="")


def _setting_enabled(conn, key, default=True):
    value = get_setting(conn, key, "1" if default else "0").strip().lower()
    return value in ("1", "true", "yes", "on")


def _notification_allowed(conn, kind, wallet_armed):
    # Test messages must always be deliverable so the user can validate the channel.
    if kind == "test":
        return True
    if _setting_enabled(conn, "notify_armed_only", True) and not wallet_armed:
        return False
    if kind == "receive":
        return _setting_enabled(conn, "notify_receive", True)
    if kind == "spend":
        return _setting_enabled(conn, "notify_spend", True)
    if kind == "spend_confirmed":
        return _setting_enabled(conn, "notify_confirmation", True)
    return True


def _status_text(status, confirmations):
    status = (status or "unknown").lower()
    if status == "mempool":
        return "MEMPOOL / unconfirmed"
    if status == "confirmed":
        return "CONFIRMED (%d confirmation%s)" % (
            int(confirmations or 0), "" if int(confirmations or 0) == 1 else "s"
        )
    if status == "detected":
        return "DETECTED / spending TX not resolved yet"
    return status.upper()


def _amount_text(amount_sats):
    if amount_sats is None:
        return None
    sats = int(amount_sats)
    return "%s sat (%.8f BTC)" % (format(sats, ","), sats / 100_000_000)


def _telegram_text(wallet_name, severity, kind, message, txid=None, status=None, confirmations=0, amount_sats=None):
    if kind == "spend_confirmed":
        icon = "🚨"
    else:
        icon = "🚨" if severity == "critical" else ("⚠️" if severity == "warning" else "ℹ️")
    labels = {
        "receive": "RECEIVE",
        "spend": "SPEND",
        "spend_confirmed": "SPEND CONFIRMED",
        "test": "TEST",
    }
    lines = ["%s XPUB Watch — %s" % (icon, labels.get(kind, kind.upper())), "", "Wallet: %s" % wallet_name]
    amount = _amount_text(amount_sats)
    if amount:
        lines.append("Amount: %s" % amount)
    if kind == "test":
        lines.append(message)
    if status:
        lines.append("Status: %s" % _status_text(status, confirmations))
    elif kind != "test":
        lines.append("Status: UNKNOWN")
    if kind != "test" and status != "confirmed":
        lines.append("Confirmations: %d" % int(confirmations or 0))
    if txid:
        lines.append("TXID: %s" % txid)
        link = _explorer_url(txid)
        if link:
            lines.append("Mempool: %s" % link)
    return "\n".join(lines)


def notify(wallet_name, severity, kind, message, txid=None, status=None, confirmations=0,
           amount_sats=None, wallet_armed=True, force=False):
    title = "XPUB Watch: %s" % wallet_name
    body = "%s %s: %s" % (severity.upper(), kind, message)
    amount = _amount_text(amount_sats)
    if amount:
        body += "\nAmount: %s" % amount
    if status:
        body += "\nStatus: %s" % _status_text(status, confirmations)
    elif kind != "test":
        body += "\nStatus: UNKNOWN"
    if txid:
        body += "\nTXID: %s" % txid
        link = _explorer_url(txid)
        if link:
            body += "\nTransaction: %s" % link

    with connect() as conn:
        if not force and not _notification_allowed(conn, kind, bool(wallet_armed)):
            log.info("notification suppressed: wallet=%s kind=%s armed=%s", wallet_name, kind, bool(wallet_armed))
            return False
        ntfy_server = get_setting(conn, "ntfy_server", "")
        ntfy_topic = get_setting(conn, "ntfy_topic", "")
        ntfy_token = get_setting(conn, "ntfy_token", "")
        gotify_server = get_setting(conn, "gotify_server", "")
        gotify_token = get_setting(conn, "gotify_token", "")
        telegram_bot_token = get_setting(conn, "telegram_bot_token", "")
        telegram_chat_id = get_setting(conn, "telegram_chat_id", "")

    if ntfy_server and ntfy_topic:
        try:
            url = ntfy_server.rstrip("/") + "/" + urllib.parse.quote(ntfy_topic, safe="")
            headers = {"Title": title, "Priority": "urgent" if severity == "critical" else "default"}
            if ntfy_token:
                headers["Authorization"] = "Bearer " + ntfy_token
            _post(url, body.encode("utf-8"), headers)
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)

    if telegram_bot_token and telegram_chat_id:
        try:
            url = "https://api.telegram.org/bot" + urllib.parse.quote(telegram_bot_token, safe=":") + "/sendMessage"
            payload = urllib.parse.urlencode({
                "chat_id": telegram_chat_id,
                "text": _telegram_text(wallet_name, severity, kind, message, txid, status, confirmations, amount_sats),
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            _post(url, payload, {"Content-Type": "application/x-www-form-urlencoded"})
        except Exception as exc:
            log.warning("Telegram notification failed: %s", exc)

    if gotify_server and gotify_token:
        try:
            url = gotify_server.rstrip("/") + "/message?token=" + urllib.parse.quote(gotify_token, safe="")
            payload = json.dumps({"title": title, "message": body, "priority": 10 if severity == "critical" else 5}).encode("utf-8")
            _post(url, payload, {"Content-Type": "application/json"})
        except Exception as exc:
            log.warning("Gotify notification failed: %s", exc)
    return True
