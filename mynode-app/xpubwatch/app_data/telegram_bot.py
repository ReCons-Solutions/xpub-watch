"""Telegram bot commands: answer balance queries from the configured chat.

Uses long polling (getUpdates), so the node needs no inbound port. Only
messages from the chat configured for alerts are answered; everything else
is ignored silently. Off by default (setting "telegram_commands").

A Telegram bot delivers updates to exactly one consumer. Use a separate bot
for every installation (e.g. one for this node, one for a WordPress site).
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from config import VERSION
from db import connect, get_runtime, get_setting, set_runtime

log = logging.getLogger("xpubwatch.telegram")

POLL_TIMEOUT = 50          # seconds Telegram keeps a getUpdates request open
MAX_MESSAGE_AGE = 300      # ignore commands older than this (backlog after downtime)
MIN_REPLY_INTERVAL = 2.0   # seconds between replies (loop / spam protection)
MAX_MESSAGE_LEN = 4000     # Telegram limit is 4096

COMMANDS = [
    ("balance", "Balance of all wallets (or: /balance <name>)"),
    ("status", "Watcher and Electrum status"),
    ("help", "Available commands"),
]
ALIASES = {
    "balance": "balance", "saldo": "balance", "kontostand": "balance", "bal": "balance",
    "status": "status", "help": "help", "start": "help", "hilfe": "help",
}


class TelegramAPIError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _btc(sats):
    return "%.8f BTC" % (int(sats or 0) / 100000000.0)


def _sat(sats):
    return format(int(sats or 0), ",")


def _ago(ts, now=None):
    if not ts:
        return "never"
    secs = max(0, int((now or time.time()) - int(ts)))
    if secs < 90:
        return "%d s ago" % secs
    if secs < 5400:
        return "%d min ago" % (secs // 60)
    return "%d h ago" % (secs // 3600)


# ------------------------------------------------------------------ texts
def wallet_balances(conn):
    return conn.execute("""
        SELECT w.id, w.name, w.network, w.armed, w.synced,
          COALESCE((SELECT SUM(value) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL),0) AS balance,
          COALESCE((SELECT SUM(value) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL AND u.height<=0),0) AS unconfirmed,
          (SELECT COUNT(*) FROM utxos u WHERE u.wallet_id=w.id AND u.spent_at IS NULL) AS utxos
        FROM wallets w ORDER BY w.id
    """).fetchall()


def balance_text(conn, query="", now=None):
    rows = wallet_balances(conn)
    if not rows:
        return "No wallets configured."
    query = (query or "").strip().lower()
    if query:
        rows = [r for r in rows if query in r["name"].lower()]
        if not rows:
            return "No wallet matches \"%s\". Send /balance for all wallets." % query[:40]
    lines = ["\U0001F4B0 XPUB Watch balance", ""]
    totals = {}
    for r in rows:
        flags = []
        if r["network"] != "main":
            flags.append("testnet")
        if not r["armed"]:
            flags.append("disarmed")
        if not r["synced"]:
            flags.append("initial sync pending")
        name = r["name"] + (" (%s)" % ", ".join(flags) if flags else "")
        lines.append("%s: %s" % (name, _btc(r["balance"])))
        detail = "  %s sat, %d UTXO%s" % (_sat(r["balance"]), r["utxos"], "" if r["utxos"] == 1 else "s")
        if r["unconfirmed"]:
            detail += ", %s unconfirmed" % _btc(r["unconfirmed"])
        lines.append(detail)
        totals[r["network"]] = totals.get(r["network"], 0) + int(r["balance"])
    if len(rows) > 1:
        lines.append("")
        lines.append("Total: %s" % _btc(totals.get("main", 0)))
        if totals.get("test"):
            lines.append("Total testnet: %s" % _btc(totals["test"]))
    lines.append("")
    lines.append(_freshness(conn, now))
    return "\n".join(lines)[:MAX_MESSAGE_LEN]


def _freshness(conn, now=None):
    last = get_runtime(conn, "last_scan")
    tip = get_runtime(conn, "tip_height")
    err = get_runtime(conn, "last_error")
    text = "Last scan: %s" % _ago(last, now)
    if tip:
        text += ", block %s" % format(int(tip), ",")
    if err:
        text += "\n⚠️ Watcher error: %s" % err[:300]
    return text


def status_text(conn, now=None):
    rows = wallet_balances(conn)
    pending = conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0]
    unacked = conn.execute("SELECT COUNT(*) FROM events WHERE acknowledged=0").fetchone()[0]
    armed = sum(1 for r in rows if r["armed"])
    lines = [
        "⚙️ XPUB Watch %s" % VERSION,
        "Wallets: %d (%d armed)" % (len(rows), armed),
        "Unacknowledged events: %d" % unacked,
        "Notifications pending: %d" % pending,
        _freshness(conn, now),
    ]
    return "\n".join(lines)


def help_text():
    lines = ["XPUB Watch commands:"]
    lines += ["/%s - %s" % c for c in COMMANDS]
    lines.append("")
    lines.append("Aliases: /saldo, /kontostand")
    return "\n".join(lines)


def parse_command(text, bot_username=""):
    """'/balance@MyBot cold' -> ('balance', 'cold'); None for non-commands."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    cmd, _, target = head[1:].partition("@")
    if target and bot_username and target.lower() != bot_username.lower():
        return None  # addressed to another bot in a group
    cmd = ALIASES.get(cmd.lower())
    if not cmd:
        return ("unknown", "")
    return (cmd, rest.strip())


def reply_for(conn, cmd, arg, now=None):
    if cmd == "balance":
        return balance_text(conn, arg, now)
    if cmd == "status":
        return status_text(conn, now)
    if cmd == "help":
        return help_text()
    return "Unknown command. Send /help."


# -------------------------------------------------------------------- bot
def commands_enabled(conn):
    return (
        get_setting(conn, "telegram_commands", "0") == "1"
        and bool(get_setting(conn, "telegram_bot_token", "").strip())
        and bool(get_setting(conn, "telegram_chat_id", "").strip())
    )


class TelegramBot(threading.Thread):
    daemon = True

    def __init__(self, api=None, sleep=None):
        super().__init__(name="xpub-telegram")
        self.stop_event = threading.Event()
        self._api = api
        self._sleep = sleep or self.stop_event.wait
        self._token = None
        self._username = ""
        self._last_reply = 0.0
        self._commands_registered_for = None

    def stop(self):
        self.stop_event.set()

    # ---------------------------------------------------------- transport
    def api(self, method, params=None, timeout=15):
        if self._api is not None:
            return self._api(method, params or {})
        url = "https://api.telegram.org/bot%s/%s" % (urllib.parse.quote(self._token, safe=":"), method)
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                raise TelegramAPIError("HTTP %d" % exc.code, exc.code)
        if not body.get("ok"):
            raise TelegramAPIError(body.get("description") or "Telegram API error", body.get("error_code"))
        return body.get("result")

    # -------------------------------------------------------------- state
    def _set_error(self, text):
        try:
            with connect() as conn:
                set_runtime(conn, "telegram_error", text)
                set_runtime(conn, "telegram_heartbeat", int(time.time()))
        except Exception:
            log.exception("storing telegram state failed")

    def _config(self):
        with connect() as conn:
            if not commands_enabled(conn):
                return None
            token = get_setting(conn, "telegram_bot_token", "").strip()
            bot_id, _, stored = (get_runtime(conn, "telegram_offset") or "").partition(":")
            return {
                "token": token,
                "bot_id": token.split(":", 1)[0],
                "chat": get_setting(conn, "telegram_chat_id", "").strip(),
                # The update offset belongs to one bot; a new token starts over.
                "offset": int(stored) if stored.isdigit() and bot_id == token.split(":", 1)[0] else 0,
            }

    def _setup(self, cfg):
        """Identify the bot and register the command menu once per token."""
        if self._commands_registered_for == cfg["token"]:
            return
        me = self.api("getMe")
        self._username = (me or {}).get("username", "")
        try:
            self.api("setMyCommands", {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS])})
        except TelegramAPIError as exc:
            log.warning("setMyCommands failed: %s", exc)
        self._commands_registered_for = cfg["token"]

    # --------------------------------------------------------------- core
    def handle_update(self, update, chat_id, now=None):
        """Return (chat_id, text) to send, or None."""
        msg = update.get("message") or update.get("edited_message")
        if not isinstance(msg, dict):
            return None
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != str(chat_id):
            log.info("ignoring Telegram message from unauthorised chat %s", chat)
            return None
        if update.get("edited_message"):
            return None
        now = now or time.time()
        if now - int(msg.get("date") or 0) > MAX_MESSAGE_AGE:
            return None
        parsed = parse_command(msg.get("text", ""), self._username)
        if parsed is None:
            return None
        with connect() as conn:
            return (chat, reply_for(conn, parsed[0], parsed[1], now))

    def poll_once(self, timeout=POLL_TIMEOUT):
        cfg = self._config()
        if cfg is None:
            self._commands_registered_for = None
            return False
        self._token = cfg["token"]
        self._setup(cfg)
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
        if cfg["offset"]:
            params["offset"] = cfg["offset"]
        updates = self.api("getUpdates", params, timeout=timeout + 15) or []
        offset = cfg["offset"]
        for upd in updates:
            offset = max(offset, int(upd.get("update_id", 0)) + 1)
            try:
                out = self.handle_update(upd, cfg["chat"])
            except Exception:
                log.exception("handling Telegram update failed")
                out = None
            if out:
                wait = MIN_REPLY_INTERVAL - (time.time() - self._last_reply)
                if wait > 0:
                    self._sleep(wait)
                self.api("sendMessage", {"chat_id": out[0], "text": out[1], "disable_web_page_preview": "true"})
                self._last_reply = time.time()
        if offset != cfg["offset"]:
            with connect() as conn:
                set_runtime(conn, "telegram_offset", "%s:%d" % (cfg["bot_id"], offset))
        self._set_error(None)
        return True

    def run(self):
        backoff = 5
        while not self.stop_event.is_set():
            try:
                active = self.poll_once()
                backoff = 5
                if not active:
                    self._sleep(15)
            except TelegramAPIError as exc:
                if exc.code == 409:
                    text = ("Telegram reports a conflict: the bot is used by another program or has a webhook "
                            "(e.g. XPUB Watch for WordPress). Use a separate bot for this node.")
                elif exc.code == 401:
                    text = "Telegram rejected the bot token."
                else:
                    text = "Telegram: %s" % exc
                log.warning(text)
                self._set_error(text)
                self._sleep(backoff)
                backoff = min(300, backoff * 2)
            except Exception as exc:
                log.warning("Telegram polling failed: %s", exc)
                self._set_error("Telegram not reachable: %s" % exc)
                self._sleep(backoff)
                backoff = min(300, backoff * 2)
