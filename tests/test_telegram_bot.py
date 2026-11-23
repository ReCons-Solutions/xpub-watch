"""Telegram balance queries (long polling) against a fake Bot API."""
import json
import time

import pytest

from helpers import XPUB, ZPUB, spk

CHAT = "4242"


class FakeTelegram:
    def __init__(self):
        self.updates = []
        self.sent = []
        self.calls = []
        self.error = None
        self.next_id = 100

    def message(self, text, chat=CHAT, age=0, edited=False):
        self.next_id += 1
        key = "edited_message" if edited else "message"
        self.updates.append({"update_id": self.next_id, key: {
            "message_id": self.next_id, "date": int(time.time()) - age,
            "chat": {"id": int(chat), "type": "private"}, "text": text}})

    def __call__(self, method, params):
        self.calls.append((method, params))
        if self.error:
            from telegram_bot import TelegramAPIError
            raise TelegramAPIError(*self.error)
        if method == "getMe":
            return {"id": 1, "is_bot": True, "username": "XpubTestBot"}
        if method == "getUpdates":
            offset = int(params.get("offset", 0))
            return [u for u in self.updates if u["update_id"] >= offset]
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {"message_id": 1}
        return True


@pytest.fixture
def tg(db):
    with db.connect() as conn:
        db.set_setting(conn, "telegram_bot_token", "123456:ABC")
        db.set_setting(conn, "telegram_chat_id", CHAT)
        db.set_setting(conn, "telegram_commands", "1")
    return FakeTelegram()


@pytest.fixture
def bot(tg):
    from telegram_bot import TelegramBot

    return TelegramBot(api=tg, sleep=lambda s: None)


def funded(db, chain, watcher):
    db.add_wallet("Cold storage", ZPUB, "main", "p2wpkh")
    db.add_wallet("Spending", XPUB, "main", "p2pkh")
    chain.pay(spk(ZPUB, 0), 150_000_000, height=chain.tip_height)
    chain.pay(spk(ZPUB, 1), 25_000)
    chain.pay(spk(XPUB, 0, script_type="p2pkh"), 50_000, height=chain.tip_height)
    watcher.scan_once()
    watcher._publish_runtime()


def test_balance_all_wallets(db, chain, watcher, tg, bot):
    funded(db, chain, watcher)
    tg.message("/balance")
    assert bot.poll_once(timeout=0)
    assert len(tg.sent) == 1
    chat, text = tg.sent[0]
    assert chat == CHAT
    assert "Cold storage: 1.50025000 BTC" in text
    assert "150,025,000 sat, 2 UTXOs, 0.00025000 BTC unconfirmed" in text
    assert "Spending: 0.00050000 BTC" in text
    assert "Total: 1.50075000 BTC" in text
    assert "Last scan:" in text and "block 800,000" in text


def test_balance_filter_and_aliases(db, chain, watcher, tg, bot):
    funded(db, chain, watcher)
    tg.message("/saldo cold")
    tg.message("/kontostand@XpubTestBot spend")
    tg.message("/balance nothing-like-this")
    bot.poll_once(timeout=0)
    texts = [t for _, t in tg.sent]
    assert "Cold storage" in texts[0] and "Spending" not in texts[0] and "Total" not in texts[0]
    assert "Spending" in texts[1] and "Cold storage" not in texts[1]
    assert texts[2].startswith("No wallet matches")


def test_other_chats_and_bots_are_ignored(db, tg, bot):
    tg.message("/balance", chat="999")
    tg.message("/balance@SomeOtherBot")
    tg.message("hello, no command")
    tg.message("/balance", edited=True)
    bot.poll_once(timeout=0)
    assert tg.sent == []


def test_backlog_is_skipped_and_offset_persisted(db, tg, bot):
    tg.message("/status", age=3600)
    tg.message("/help")
    bot.poll_once(timeout=0)
    assert len(tg.sent) == 1 and "/balance" in tg.sent[0][1]
    bot.poll_once(timeout=0)
    assert len(tg.sent) == 1, "updates must be confirmed via offset"
    params = [p for m, p in tg.calls if m == "getUpdates"][-1]
    assert params["offset"] == tg.next_id + 1
    with db.connect() as conn:
        assert db.get_runtime(conn, "telegram_offset") == "123456:%d" % (tg.next_id + 1)


def test_new_token_resets_offset(db, tg, bot):
    tg.message("/help")
    bot.poll_once(timeout=0)
    with db.connect() as conn:
        db.set_setting(conn, "telegram_bot_token", "777:XYZ")
    bot.poll_once(timeout=0)
    params = [p for m, p in tg.calls if m == "getUpdates"][-1]
    assert "offset" not in params
    assert [m for m, _ in tg.calls].count("getMe") == 2, "commands registered again for the new bot"


def test_status_and_unknown(db, chain, watcher, tg, bot):
    funded(db, chain, watcher)
    tg.message("/status")
    tg.message("/foo")
    bot.poll_once(timeout=0)
    assert "Wallets: 2 (2 armed)" in tg.sent[0][1]
    assert tg.sent[1][1] == "Unknown command. Send /help."


def test_disabled_by_default(db, tg):
    from telegram_bot import TelegramBot

    with db.connect() as conn:
        db.set_setting(conn, "telegram_commands", "0")
    bot = TelegramBot(api=tg, sleep=lambda s: None)
    tg.message("/balance")
    assert bot.poll_once(timeout=0) is False
    assert tg.calls == []


def test_command_menu_registered(db, tg, bot):
    bot.poll_once(timeout=0)
    cmds = [p for m, p in tg.calls if m == "setMyCommands"]
    assert [c["command"] for c in json.loads(cmds[0]["commands"])] == ["balance", "status", "help"]


def test_conflict_is_reported(db, tg, bot):
    tg.error = ("Conflict: can't use getUpdates method while webhook is active", 409)
    bot._sleep = lambda s: bot.stop()
    bot.run()
    with db.connect() as conn:
        assert "separate bot" in db.get_runtime(conn, "telegram_error")


def test_settings_ui(db):
    import re

    import app as appmod

    client = appmod.create_app(start_watcher=False).test_client()
    html = client.get("/setup").get_data(as_text=True)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    client.post("/setup", data={"csrf_token": tok, "password": "a long password", "password2": "a long password"})
    html = client.get("/").get_data(as_text=True)
    assert 'name="telegram_commands"' in html and "checked" not in html.split('name="telegram_commands"')[1][:40]
    tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    client.post("/settings", data={"csrf_token": tok, "telegram_commands": "1", "telegram_chat_id": CHAT})
    with db.connect() as conn:
        assert db.get_setting(conn, "telegram_commands") == "1"
        db.set_runtime(conn, "telegram_error", "Telegram rejected the bot token.")
    assert "Telegram rejected the bot token." in client.get("/").get_data(as_text=True)


def test_http_transport(db, monkeypatch):
    import io
    import urllib.error

    import telegram_bot

    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["data"], seen["timeout"] = req.full_url, req.data.decode(), timeout
        if "getMe" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                         io.BytesIO(b'{"ok":false,"error_code":401,"description":"Unauthorized"}'))
        return Resp(b'{"ok":true,"result":[]}')

    monkeypatch.setattr(telegram_bot.urllib.request, "urlopen", fake_urlopen)
    bot = telegram_bot.TelegramBot()
    bot._token = "123:A/B"
    assert bot.api("getUpdates", {"timeout": 50}, timeout=65) == []
    assert seen["url"] == "https://api.telegram.org/bot123:A%2FB/getUpdates" and seen["timeout"] == 65
    with pytest.raises(telegram_bot.TelegramAPIError) as exc:
        bot.api("getMe")
    assert exc.value.code == 401
