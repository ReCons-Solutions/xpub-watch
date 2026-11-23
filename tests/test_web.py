"""Web UI: authentication, CSRF, secret handling, wallet validation."""
import re

import pytest

from helpers import XPUB, YPUB, ZPUB

PASSWORD = "correct horse battery"


@pytest.fixture
def web(db):
    import app as appmod
    import auth

    auth.throttle._state.clear()
    flask_app = appmod.create_app(start_watcher=False)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def csrf(client, path="/"):
    html = client.get(path, follow_redirects=True).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "no csrf token on %s" % path
    return m.group(1)


@pytest.fixture
def logged_in(web):
    token = csrf(web, "/setup")
    r = web.post("/setup", data={"csrf_token": token, "password": PASSWORD, "password2": PASSWORD})
    assert r.status_code == 302
    return web


def post(client, path, **data):
    data["csrf_token"] = csrf(client)
    return client.post(path, data=data, follow_redirects=True)


# ------------------------------------------------------------------ auth
def test_fresh_install_requires_password_setup(web):
    assert web.get("/").headers["Location"].endswith("/setup")
    token = csrf(web, "/setup")
    r = web.post("/setup", data={"csrf_token": token, "password": "short", "password2": "short"},
                 follow_redirects=True)
    assert "at least 10" in r.get_data(as_text=True)
    r = web.post("/setup", data={"csrf_token": token, "password": PASSWORD, "password2": PASSWORD})
    assert r.headers["Location"].endswith("/")
    assert web.get("/").status_code == 200


def test_setup_cannot_be_repeated(logged_in):
    logged_in.post("/logout", data={"csrf_token": csrf(logged_in)})
    assert logged_in.get("/setup").headers["Location"].endswith("/")
    r = logged_in.post("/setup", data={"csrf_token": csrf(logged_in, "/login"),
                                       "password": "attacker-password", "password2": "attacker-password"})
    assert r.status_code == 302
    import auth
    assert auth.verify_password(PASSWORD)


def test_login_logout_and_throttle(logged_in):
    web = logged_in
    web.post("/logout", data={"csrf_token": csrf(web)})
    assert web.get("/").headers["Location"].endswith("/login")
    token = csrf(web, "/login")
    for _ in range(5):
        assert web.post("/login", data={"csrf_token": token, "password": "wrong"}).status_code == 401
    r = web.post("/login", data={"csrf_token": token, "password": PASSWORD})
    assert r.status_code == 429, "correct password must also be refused while throttled"
    import auth
    auth.throttle._state.clear()
    assert web.post("/login", data={"csrf_token": token, "password": PASSWORD}).status_code == 302
    assert web.get("/").status_code == 200


def test_post_without_csrf_is_rejected(logged_in):
    r = logged_in.post("/wallets", data={"name": "x", "xpub": ZPUB, "script_type": "auto"})
    assert r.status_code == 400
    r = logged_in.post("/wallets", data={"name": "x", "xpub": ZPUB, "script_type": "auto", "csrf_token": "forged"})
    assert r.status_code == 400
    with __import__("db").connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM wallets").fetchone()[0] == 0


def test_change_password(logged_in):
    r = post(logged_in, "/password", current_password="nope", new_password="x" * 12, new_password2="x" * 12)
    assert "Current password is wrong" in r.get_data(as_text=True)
    r = post(logged_in, "/password", current_password=PASSWORD, new_password="new password 123",
             new_password2="new password 123")
    assert "Password changed" in r.get_data(as_text=True)
    import auth
    assert auth.verify_password("new password 123") and not auth.verify_password(PASSWORD)


def test_security_headers_and_cookie(logged_in):
    r = logged_in.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"
    import app as appmod
    secure_app = appmod.create_app(start_watcher=False)
    secure_app.config["SESSION_COOKIE_SECURE"] = True
    c = secure_app.test_client()
    cookie = c.get("/login").headers.get("Set-Cookie", "")
    assert cookie.startswith("xpubwatch_session=")
    for flag in ("HttpOnly", "Secure", "SameSite=Strict"):
        assert flag in cookie


def test_health_hides_details_when_logged_out(web):
    body = web.get("/health").get_json()
    assert set(body) == {"ok"}


# --------------------------------------------------------------- secrets
def test_secrets_are_write_only(logged_in, db):
    post(logged_in, "/settings", telegram_bot_token="123456:SECRET-TOKEN", telegram_chat_id="42",
         ntfy_server="", gotify_server="", explorer_tx_url="")
    html = logged_in.get("/").get_data(as_text=True)
    assert "SECRET-TOKEN" not in html and "configured" in html
    post(logged_in, "/settings", telegram_bot_token="", telegram_chat_id="42")
    with db.connect() as conn:
        assert db.get_setting(conn, "telegram_bot_token") == "123456:SECRET-TOKEN"
    post(logged_in, "/settings", telegram_bot_token="", clear_telegram_bot_token="1", telegram_chat_id="42")
    with db.connect() as conn:
        assert db.get_setting(conn, "telegram_bot_token") == ""


def test_javascript_urls_rejected(logged_in, db):
    r = post(logged_in, "/settings", explorer_tx_url="javascript:alert(1)//")
    assert "must start with https://" in r.get_data(as_text=True)
    with db.connect() as conn:
        assert db.get_setting(conn, "explorer_tx_url") == ""


# ---------------------------------------------------------------- wallets
def wallets(db):
    with db.connect() as conn:
        return conn.execute("SELECT name, network, script_type FROM wallets ORDER BY id").fetchall()


def test_zpub_auto_detects_script_type(logged_in, db):
    r = post(logged_in, "/wallets", name="Main", xpub=ZPUB, script_type="auto")
    assert "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu" in r.get_data(as_text=True)
    assert [tuple(w) for w in wallets(db)] == [("Main", "main", "p2wpkh")]


def test_ypub_with_wrong_script_type_rejected(logged_in, db):
    r = post(logged_in, "/wallets", name="Nested", xpub=YPUB, script_type="p2wpkh")
    assert "implies" in r.get_data(as_text=True)
    assert wallets(db) == []


def test_xpub_requires_explicit_script_type(logged_in, db):
    r = post(logged_in, "/wallets", name="Legacy", xpub=XPUB, script_type="auto")
    assert "does not encode the address type" in r.get_data(as_text=True)
    post(logged_in, "/wallets", name="Legacy", xpub=XPUB, script_type="p2pkh")
    assert [tuple(w) for w in wallets(db)] == [("Legacy", "main", "p2pkh")]


def test_duplicate_key_rejected_even_with_other_prefix(logged_in, db):
    from embit import base58

    post(logged_in, "/wallets", name="A", xpub=ZPUB, script_type="auto")
    # Same key re-encoded as xpub, same script type -> duplicate.
    as_xpub = base58.encode_check(bytes.fromhex("0488b21e") + base58.decode_check(ZPUB)[4:])
    r = post(logged_in, "/wallets", name="B", xpub=as_xpub, script_type="p2wpkh")
    assert "already watched" in r.get_data(as_text=True)
    assert len(wallets(db)) == 1


def test_private_key_rejected_in_ui(logged_in, db):
    from embit import bip32, bip39

    xprv = bip32.HDKey.from_seed(bip39.mnemonic_to_seed("abandon " * 11 + "about")).to_base58()
    r = post(logged_in, "/wallets", name="Oops", xpub=xprv, script_type="p2wpkh")
    assert "Private extended keys are not accepted" in r.get_data(as_text=True)
    assert wallets(db) == []


def test_xpub_never_rendered(logged_in, db):
    post(logged_in, "/wallets", name="Main", xpub=ZPUB, script_type="auto")
    assert ZPUB not in logged_in.get("/").get_data(as_text=True)


def test_delete_unknown_wallet_404(logged_in):
    r = logged_in.post("/wallets/99/delete", data={"csrf_token": csrf(logged_in)})
    assert r.status_code == 404


def test_watcher_runs_in_one_process_only(db, monkeypatch, tmp_path):
    import fcntl
    import os

    import app as appmod

    monkeypatch.setattr(appmod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(appmod, "_watcher", None)
    holder = open(os.path.join(str(tmp_path), "watcher.lock"), "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)  # "another worker" holds the lock
    try:
        assert appmod.start_watcher_once() is None
    finally:
        holder.close()


def test_reset_password_command(logged_in, db, capsys):
    import auth
    import manage

    assert manage.main(["manage.py", "reset-password"]) == 0
    assert not auth.password_configured()
    assert logged_in.get("/").headers["Location"].endswith("/setup")
