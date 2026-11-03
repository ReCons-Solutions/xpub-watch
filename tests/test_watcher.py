"""End-to-end watcher scenarios against the in-memory Electrum backend.

Tests marked ``xfail(strict=True)`` reproduce known defects of 0.4.0-beta.1.
They must fail on the old code; the fix removes the marker.
"""
import pytest

from helpers import XPUB, ZPUB, events, kinds, spk


def add(dbmod, xpub=ZPUB, script_type="p2wpkh", name="Main"):
    return dbmod.add_wallet(name, xpub, "main", script_type)


def balance(dbmod, wallet_id):
    with dbmod.connect() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(value),0) FROM utxos WHERE wallet_id=? AND spent_at IS NULL", (wallet_id,)
        ).fetchone()[0]


# ---------------------------------------------------------------------------
# Baseline behaviour (must keep working)
# ---------------------------------------------------------------------------

def test_receive_mempool_then_confirm(db, chain, watcher, sent):
    wid = add(db)
    watcher.scan_once()
    txid, _ = chain.pay(spk(ZPUB, 0), 50_000)
    watcher.scan_once()
    assert kinds(sent) == ["receive"]
    ev = events(db, "receive")[-1]
    assert ev["txid"] == txid and ev["status"] == "mempool" and ev["confirmations"] == 0

    chain.confirm(txid)
    chain.mine(2)
    watcher.scan_once()
    ev = events(db, "receive")[-1]
    assert ev["status"] == "confirmed" and ev["confirmations"] == 3
    assert kinds(sent) == ["receive"], "confirmation of a receive must not re-notify"
    assert balance(db, wid) == 50_000


def test_spend_mempool_then_confirm(db, chain, watcher, sent):
    wid = add(db)
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 80_000, height=chain.tip_height)
    watcher.scan_once()
    spend_txid = chain.spend([op])
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend"]
    ev = events(db, "spend")[-1]
    assert ev["severity"] == "critical" and ev["txid"] == spend_txid and ev["status"] == "mempool"
    assert balance(db, wid) == 0

    chain.confirm(spend_txid)
    watcher.scan_once()
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend", "spend_confirmed"]


def test_disarmed_wallet_is_silent_but_logged(db, chain, watcher, sent):
    wid = add(db)
    with db.connect() as conn:
        conn.execute("UPDATE wallets SET armed=0 WHERE id=?", (wid,))
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 1, branch=1), 10_000)
    watcher.scan_once()
    chain.spend([op])
    watcher.scan_once()
    assert sent == []
    assert [e["kind"] for e in events(db)][-2:] == ["receive", "spend"]
    assert events(db, "spend")[-1]["severity"] == "warning"


def test_change_branch_and_lookahead(db, chain, watcher, sent):
    wid = add(db)
    watcher.scan_once()
    chain.pay(spk(ZPUB, 7, branch=1), 1_000)
    chain.pay(spk(ZPUB, 19), 2_000)
    watcher.scan_once()
    watcher.scan_once()
    assert balance(db, wid) == 3_000


# ---------------------------------------------------------------------------
# Known defects of 0.4.0-beta.1
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="A1: rollback after notify causes duplicate alerts")
def test_no_duplicate_alert_when_later_wallet_fails(db, chain, watcher, sent):
    add(db, ZPUB, "p2wpkh", "First")
    add(db, XPUB, "p2pkh", "Second")
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 30_000, height=chain.tip_height)
    watcher.scan_once()
    chain.spend([op])
    chain.fail_scripthashes = {_scripthash(spk(XPUB, 0, script_type="p2pkh"))}
    for _ in range(3):
        try:
            watcher.scan_once()
        except Exception:
            pass
    assert kinds(sent).count("spend") == 1


@pytest.mark.xfail(strict=True, reason="A2: dropped spend re-announces the UTXO as a new receive")
def test_dropped_spend_is_reported_as_reverted_not_receive(db, chain, watcher, sent):
    add(db)
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 40_000, height=chain.tip_height)
    watcher.scan_once()
    spend_txid = chain.spend([op])
    watcher.scan_once()
    chain.drop(spend_txid)
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend", "spend_reverted"]


@pytest.mark.xfail(strict=True, reason="dropped unconfirmed receive raises a false SPEND alarm")
def test_dropped_incoming_tx_is_not_a_spend(db, chain, watcher, sent):
    add(db)
    watcher.scan_once()
    txid, _ = chain.pay(spk(ZPUB, 0), 40_000)
    watcher.scan_once()
    chain.drop(txid)
    watcher.scan_once()
    assert "spend" not in kinds(sent)
    assert kinds(sent) == ["receive", "receive_dropped"]


@pytest.mark.xfail(strict=True, reason="A3: receive+spend between two polls is invisible")
def test_receive_and_spend_between_polls(db, chain, watcher, sent):
    add(db)
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 2), 25_000)
    chain.spend([op])
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend"]


@pytest.mark.xfail(strict=True, reason="RBF replacement of a spend is not tracked")
def test_rbf_replaced_spend_is_tracked(db, chain, watcher, sent):
    add(db)
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 60_000, height=chain.tip_height)
    watcher.scan_once()
    first = chain.spend([op])
    watcher.scan_once()
    chain.drop(first)
    second = chain.spend([op], value=55_000)
    watcher.scan_once()
    assert events(db, "spend")[-1]["txid"] == first
    assert events(db, "spend_replaced")[-1]["txid"] == second
    chain.confirm(second)
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend", "spend_replaced", "spend_confirmed"]


@pytest.mark.xfail(strict=True, reason="importing a funded wallet floods receive alerts")
def test_import_of_funded_wallet_is_silent(db, chain, watcher, sent):
    for i in range(5):
        chain.pay(spk(ZPUB, i), 1_000 * (i + 1), height=chain.tip_height - 10)
    wid = add(db)
    watcher.scan_once()
    assert sent == []
    assert balance(db, wid) == 15_000


@pytest.mark.xfail(strict=True, reason="gap-limit discovery needs several poll cycles")
def test_gap_discovery_in_single_scan(db, chain, watcher, sent):
    chain.pay(spk(ZPUB, 15), 1_000, height=chain.tip_height)
    chain.pay(spk(ZPUB, 30), 2_000, height=chain.tip_height)
    chain.pay(spk(ZPUB, 45), 4_000, height=chain.tip_height)
    wid = add(db)
    watcher.scan_once()
    assert balance(db, wid) == 7_000


def _scripthash(script_pubkey):
    from fake_electrum import scripthash_of

    return scripthash_of(script_pubkey)
