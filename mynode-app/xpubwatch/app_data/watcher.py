import logging
import threading
import time
from config import ELECTRUM_HOST, ELECTRUM_PORT, LOOKAHEAD, POLL_SECONDS
from db import connect, event
from electrum_client import ElectrumClient
from notifier import notify
from wallet import derive_address_record

log = logging.getLogger("xpubwatch")


def tx_status(height):
    return "confirmed" if int(height or 0) > 0 else "mempool"


def confirmations(height, tip_height):
    h = int(height or 0)
    tip = int(tip_height or 0)
    if h <= 0 or tip <= 0 or h > tip:
        return 0
    return tip - h + 1


class Watcher(threading.Thread):
    daemon = True

    def __init__(self, client=None):
        super().__init__(name="xpub-watcher")
        self.stop_event = threading.Event()
        self.client = client or ElectrumClient(ELECTRUM_HOST, ELECTRUM_PORT)
        self.last_error = None
        self.last_scan = None
        self.tip_height = None
        self.consecutive_errors = 0

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.scan_once()
                self.consecutive_errors = 0
                self.last_error = None
                self.last_scan = int(time.time())
            except Exception as exc:
                self.consecutive_errors += 1
                # Avoid a red health banner for a single transient local electrs stall.
                # Two consecutive failed scans still surface the fault promptly.
                if self.consecutive_errors >= 2:
                    self.last_error = str(exc)
                log.exception("watch scan failed (consecutive=%d)", self.consecutive_errors)
            self.stop_event.wait(POLL_SECONDS)

    def scan_once(self):
        self.client.version()
        tip = self.client.tip() or {}
        self.tip_height = tip.get("height")
        self.scan_all()

    def ensure_lookahead(self, conn, wallet):
        for branch in (0, 1):
            row = conn.execute(
                "SELECT COALESCE(MAX(idx),-1) AS m FROM addresses WHERE wallet_id=? AND branch=? AND used=1",
                (wallet["id"], branch),
            ).fetchone()
            last_used = row["m"]
            target = max(LOOKAHEAD - 1, last_used + LOOKAHEAD)
            existing = {
                r["idx"] for r in conn.execute(
                    "SELECT idx FROM addresses WHERE wallet_id=? AND branch=?",
                    (wallet["id"], branch),
                )
            }
            for idx in range(target + 1):
                if idx in existing:
                    continue
                rec = derive_address_record(wallet["xpub"], branch, idx, wallet["network"], wallet["script_type"])
                conn.execute(
                    "INSERT OR IGNORE INTO addresses(wallet_id,branch,idx,address,scripthash) VALUES(?,?,?,?,?)",
                    (wallet["id"], branch, idx, rec["address"], rec["scripthash"]),
                )

    def scan_all(self):
        with connect() as conn:
            wallets = conn.execute("SELECT * FROM wallets ORDER BY id").fetchall()
            for wallet in wallets:
                self.ensure_lookahead(conn, wallet)
                self.scan_wallet(conn, wallet)
                self.ensure_lookahead(conn, wallet)

    def _find_spending_tx(self, scripthash, old_txid, old_vout):
        history = self.client.history(scripthash) or []
        for item in reversed(history):
            txid = item.get("tx_hash")
            if not txid or txid == old_txid:
                continue
            try:
                verbose = self.client.transaction(txid, True)
            except Exception:
                continue
            if not isinstance(verbose, dict):
                continue
            for vin in verbose.get("vin", []):
                prev_txid = vin.get("txid") or vin.get("tx_hash")
                prev_vout = vin.get("vout")
                if prev_vout is None:
                    prev_vout = vin.get("tx_pos")
                if prev_txid == old_txid and prev_vout is not None and int(prev_vout) == int(old_vout):
                    return txid, int(item.get("height") or 0)
        return None, None

    def _refresh_event_confirmations(self, conn, wallet_id, kind, txid, height):
        status = tx_status(height)
        confs = confirmations(height, self.tip_height)
        conn.execute(
            "UPDATE events SET status=?,confirmations=? WHERE wallet_id=? AND kind=? AND txid=?",
            (status, confs, wallet_id, kind, txid),
        )
        return status, confs

    def scan_wallet(self, conn, wallet):
        addresses = conn.execute("SELECT * FROM addresses WHERE wallet_id=?", (wallet["id"],)).fetchall()
        current = {}
        history_heights = {}
        for addr in addresses:
            history = self.client.history(addr["scripthash"]) or []
            if history:
                conn.execute(
                    "UPDATE addresses SET used=1 WHERE wallet_id=? AND branch=? AND idx=?",
                    (wallet["id"], addr["branch"], addr["idx"]),
                )
            for item in history:
                txid = item.get("tx_hash")
                if txid:
                    history_heights[txid] = int(item.get("height") or 0)
            for u in self.client.listunspent(addr["scripthash"]) or []:
                key = (u["tx_hash"], int(u["tx_pos"]))
                current[key] = (u, addr)

        # Receive events must keep counting confirmations even after their UTXO is spent.
        # listunspent no longer returns a spent output, so refreshing receive events only
        # from current UTXOs freezes their confirmation count at the spend time.
        # Refresh every known receive event from Electrum history on every scan instead.
        receive_events = conn.execute(
            "SELECT DISTINCT txid FROM events WHERE wallet_id=? AND kind='receive' AND txid IS NOT NULL",
            (wallet["id"],),
        ).fetchall()
        for ev in receive_events:
            txid = ev["txid"]
            if txid in history_heights:
                self._refresh_event_confirmations(
                    conn, wallet["id"], "receive", txid, history_heights[txid]
                )

        previous = {
            (r["tx_hash"], r["tx_pos"]): r
            for r in conn.execute("SELECT * FROM utxos WHERE wallet_id=? AND spent_at IS NULL", (wallet["id"],))
        }

        for key, (u, addr) in current.items():
            status = tx_status(u.get("height"))
            confs = confirmations(u.get("height"), self.tip_height)
            if key not in previous:
                conn.execute(
                    "INSERT OR IGNORE INTO utxos(wallet_id,tx_hash,tx_pos,value,height,scripthash,first_seen) VALUES(?,?,?,?,?,?,?)",
                    (wallet["id"], u["tx_hash"], u["tx_pos"], u["value"], u["height"], addr["scripthash"], int(time.time())),
                )
                msg = "New UTXO: %.8f BTC (%s sat) to %s" % (u["value"]/100_000_000, format(u["value"], ","), addr["address"])
                event(conn, wallet["id"], "info", "receive", msg, u["tx_hash"], status, confs)
                log.info("%s: receive %d sat %s (%s, %d conf)", wallet["name"], u["value"], u["tx_hash"], status, confs)
                notify(wallet["name"], "info", "receive", msg, u["tx_hash"], status, confs, amount_sats=u["value"], wallet_armed=bool(wallet["armed"]))
            else:
                conn.execute(
                    "UPDATE utxos SET height=? WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (u["height"], wallet["id"], key[0], key[1]),
                )
                self._refresh_event_confirmations(conn, wallet["id"], "receive", u["tx_hash"], u.get("height"))

        for key, old in previous.items():
            if key in current:
                continue
            now = int(time.time())
            spend_txid, spend_height = self._find_spending_tx(old["scripthash"], key[0], key[1])
            status = tx_status(spend_height) if spend_txid else "detected"
            confs = confirmations(spend_height, self.tip_height) if spend_txid else 0
            conn.execute(
                "UPDATE utxos SET spent_at=?,spend_txid=?,spend_height=? WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                (now, spend_txid, spend_height, wallet["id"], key[0], key[1]),
            )
            severity = "critical" if wallet["armed"] else "warning"
            msg = "UTXO spent: %.8f BTC (%s sat) from %s:%s" % (
                old["value"]/100_000_000, format(old["value"], ","), old["tx_hash"], old["tx_pos"]
            )
            event(conn, wallet["id"], severity, "spend", msg, spend_txid, status, confs)
            log.log(logging.CRITICAL if severity == "critical" else logging.WARNING,
                    "%s: spend %d sat from %s:%s spending_tx=%s status=%s confirmations=%d",
                    wallet["name"], old["value"], old["tx_hash"], old["tx_pos"], spend_txid or "unknown", status, confs)
            notify(wallet["name"], severity, "spend", msg, spend_txid, status, confs, amount_sats=old["value"], wallet_armed=bool(wallet["armed"]))

        # Track confirmation count for known spend transactions. Send exactly one follow-up
        # notification when a spend moves from mempool/unconfirmed to its first confirmation.
        spent = conn.execute(
            "SELECT * FROM utxos WHERE wallet_id=? AND spent_at IS NOT NULL AND spend_txid IS NOT NULL",
            (wallet["id"],),
        ).fetchall()
        for old in spent:
            found_height = int(old["spend_height"] or 0)
            for item in self.client.history(old["scripthash"]) or []:
                if item.get("tx_hash") == old["spend_txid"]:
                    found_height = int(item.get("height") or 0)
                    break
            status, confs = self._refresh_event_confirmations(conn, wallet["id"], "spend", old["spend_txid"], found_height)
            if found_height != int(old["spend_height"] or 0):
                conn.execute(
                    "UPDATE utxos SET spend_height=? WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (found_height, wallet["id"], old["tx_hash"], old["tx_pos"]),
                )
            if found_height > 0 and not int(old["spend_confirm_notified"] or 0):
                severity = "critical" if wallet["armed"] else "warning"
                msg = "Spending transaction confirmed: %.8f BTC (%s sat) from %s:%s" % (
                    old["value"]/100_000_000, format(old["value"], ","), old["tx_hash"], old["tx_pos"]
                )
                event(conn, wallet["id"], severity, "spend_confirmed", msg, old["spend_txid"], "confirmed", confs)
                notify(wallet["name"], severity, "spend_confirmed", msg, old["spend_txid"], "confirmed", confs, amount_sats=old["value"], wallet_armed=bool(wallet["armed"]))
                conn.execute(
                    "UPDATE utxos SET spend_confirm_notified=1 WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (wallet["id"], old["tx_hash"], old["tx_pos"]),
                )
                log.info("%s: spend confirmed %s confirmations=%d", wallet["name"], old["spend_txid"], confs)
