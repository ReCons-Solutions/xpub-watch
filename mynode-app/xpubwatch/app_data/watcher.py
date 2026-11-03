import json
import logging
import threading
import time

import ledger
import notifier
from config import ELECTRUM_HOST, ELECTRUM_PORT, ELECTRUM_TIMEOUT, LOOKAHEAD, POLL_SECONDS
from db import connect, event, set_runtime
from electrum_client import ElectrumClient
from wallet import derive_address_record, parse_raw_tx

log = logging.getLogger("xpubwatch")

TX_FETCH_CHUNK = 50
MAX_DISCOVERY_ROUNDS = 200  # 200 * LOOKAHEAD addresses per branch; guards against runaway loops


def _btc(sats):
    return "%.8f BTC (%s sat)" % (sats / 100000000.0, format(int(sats), ","))


class ScanError(RuntimeError):
    pass


class Watcher(threading.Thread):
    daemon = True

    def __init__(self, client=None):
        super().__init__(name="xpub-watcher")
        self.stop_event = threading.Event()
        self.client = client or ElectrumClient(ELECTRUM_HOST, ELECTRUM_PORT, timeout=ELECTRUM_TIMEOUT)
        self.last_error = None
        self.last_scan = None
        self.tip_height = None
        self.consecutive_errors = 0
        self._parsed = {}  # txid -> parsed tx (in-memory cache over tx_cache)

    def stop(self):
        self.stop_event.set()

    # ------------------------------------------------------------------ loop
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
                if self.consecutive_errors >= 2:
                    self.last_error = str(exc)
                log.exception("watch scan failed (consecutive=%d)", self.consecutive_errors)
            self._publish_runtime()
            notifier.dispatch_pending()
            self.stop_event.wait(POLL_SECONDS)
        try:
            self.client.close()
        except Exception:
            pass

    def _publish_runtime(self):
        try:
            with connect() as conn:
                set_runtime(conn, "last_scan", self.last_scan)
                set_runtime(conn, "last_error", self.last_error)
                set_runtime(conn, "tip_height", self.tip_height)
                set_runtime(conn, "heartbeat", int(time.time()))
        except Exception:
            log.exception("publishing watcher state failed")

    def scan_once(self):
        """Scan all wallets once. Each wallet commits independently; a failing
        wallet does not roll back or block the others."""
        self.client.version()
        tip = self.client.tip() or {}
        self.tip_height = tip.get("height")
        with connect() as conn:
            wallets = conn.execute("SELECT * FROM wallets ORDER BY id").fetchall()
        errors = []
        for wallet in wallets:
            try:
                self.scan_wallet(wallet)
            except Exception as exc:
                log.exception("scan of wallet %s failed", wallet["name"])
                errors.append("%s: %s" % (wallet["name"], exc))
            notifier.dispatch_pending()
        if errors:
            raise ScanError("; ".join(errors))

    # ------------------------------------------------------------- discovery
    def ensure_lookahead(self, conn, wallet):
        """Derive addresses up to LOOKAHEAD past the last used index. Returns new rows."""
        added = []
        for branch in (0, 1):
            row = conn.execute(
                "SELECT COALESCE(MAX(idx),-1) AS m FROM addresses WHERE wallet_id=? AND branch=? AND used=1",
                (wallet["id"], branch),
            ).fetchone()
            target = max(LOOKAHEAD - 1, row["m"] + LOOKAHEAD)
            top = conn.execute(
                "SELECT COALESCE(MAX(idx),-1) AS m FROM addresses WHERE wallet_id=? AND branch=?",
                (wallet["id"], branch),
            ).fetchone()["m"]
            for idx in range(top + 1, target + 1):
                rec = derive_address_record(wallet["xpub"], branch, idx, wallet["network"], wallet["script_type"])
                conn.execute(
                    "INSERT OR IGNORE INTO addresses(wallet_id,branch,idx,address,scripthash) VALUES(?,?,?,?,?)",
                    (wallet["id"], branch, idx, rec["address"], rec["scripthash"]),
                )
                added.append(rec["scripthash"])
        return added

    def _sync_histories(self, wallet):
        """Refresh address histories whose Electrum status changed, extending
        the look-ahead window until no new used address appears."""
        with connect() as conn:
            self.ensure_lookahead(conn, wallet)
            rows = conn.execute(
                "SELECT scripthash, status FROM addresses WHERE wallet_id=?", (wallet["id"],)
            ).fetchall()
        pending = [(r["scripthash"], r["status"]) for r in rows]

        for _ in range(MAX_DISCOVERY_ROUNDS):
            if not pending:
                return
            statuses = self.client.subscribe_many([sh for sh, _ in pending])
            changed = [
                (sh, status or "-")
                for (sh, stored), status in zip(pending, statuses)
                if (status or "-") != stored
            ]
            if not changed:
                return
            histories = self.client.history_many([sh for sh, _ in changed])
            txids = sorted({h["tx_hash"] for hist in histories for h in hist or [] if h.get("tx_hash")})
            self._fetch_transactions(txids)

            with connect() as conn:
                for (sh, status), hist in zip(changed, histories):
                    hist = hist or []
                    conn.execute("DELETE FROM addr_txs WHERE wallet_id=? AND scripthash=?", (wallet["id"], sh))
                    conn.executemany(
                        "INSERT OR REPLACE INTO addr_txs(wallet_id,scripthash,txid,height) VALUES(?,?,?,?)",
                        [(wallet["id"], sh, h["tx_hash"], int(h.get("height") or 0)) for h in hist if h.get("tx_hash")],
                    )
                    conn.execute(
                        "UPDATE addresses SET status=?, used=CASE WHEN ? THEN 1 ELSE used END"
                        " WHERE wallet_id=? AND scripthash=?",
                        (status, 1 if hist else 0, wallet["id"], sh),
                    )
                added = self.ensure_lookahead(conn, wallet)
            pending = [(sh, None) for sh in added]
        raise ScanError("address discovery did not converge for wallet %s" % wallet["name"])

    def _fetch_transactions(self, txids):
        if not txids:
            return
        with connect() as conn:
            known = set()
            for i in range(0, len(txids), 500):
                chunk = txids[i:i + 500]
                known.update(
                    r["txid"] for r in conn.execute(
                        "SELECT txid FROM tx_cache WHERE txid IN (%s)" % ",".join("?" * len(chunk)), chunk
                    )
                )
        missing = [t for t in txids if t not in known]
        for i in range(0, len(missing), TX_FETCH_CHUNK):
            chunk = missing[i:i + TX_FETCH_CHUNK]
            raws = self.client.transactions_raw(chunk)
            rows = []
            for txid, raw in zip(chunk, raws):
                data = parse_raw_tx(raw)
                self._parsed[txid] = data
                rows.append((txid, json.dumps(data, separators=(",", ":"))))
            with connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO tx_cache(txid,data) VALUES(?,?)", rows)

    def _load_parsed(self, conn, txids):
        missing = [t for t in txids if t not in self._parsed]
        for i in range(0, len(missing), 500):
            chunk = missing[i:i + 500]
            for r in conn.execute(
                "SELECT txid, data FROM tx_cache WHERE txid IN (%s)" % ",".join("?" * len(chunk)), chunk
            ):
                self._parsed[r["txid"]] = json.loads(r["data"])
        if len(self._parsed) > 20000:
            keep = set(txids)
            self._parsed = {k: v for k, v in self._parsed.items() if k in keep}
        return self._parsed

    # ------------------------------------------------------------- reconcile
    def scan_wallet(self, wallet):
        self._sync_histories(wallet)
        with connect() as conn:
            wallet = conn.execute("SELECT * FROM wallets WHERE id=?", (wallet["id"],)).fetchone()
            if wallet is None:
                return  # deleted while scanning
            self._reconcile(conn, wallet)

    def _reconcile(self, conn, wallet):
        wid = wallet["id"]
        tx_heights = {}
        for r in conn.execute("SELECT txid, MAX(height) AS height FROM addr_txs WHERE wallet_id=? GROUP BY txid", (wid,)):
            tx_heights[r["txid"]] = int(r["height"] or 0)
        own = {r["scripthash"]: r["address"] for r in conn.execute(
            "SELECT scripthash, address FROM addresses WHERE wallet_id=?", (wid,))}
        parsed = self._load_parsed(conn, list(tx_heights))
        missing = [t for t in tx_heights if t not in parsed]
        if missing:
            raise ScanError("%d transactions missing from cache" % len(missing))

        truth = ledger.compute(tx_heights, parsed, set(own))
        stored = {
            (r["tx_hash"], int(r["tx_pos"])): r
            for r in conn.execute("SELECT * FROM utxos WHERE wallet_id=?", (wid,))
        }
        baseline = not int(wallet["synced"] or 0)
        changes = ledger.reconcile(truth, stored, baseline)
        tip = self.tip_height
        now = int(time.time())
        armed = bool(wallet["armed"])
        alarm = "critical" if armed else "warning"

        def emit(severity, kind, message, txid, height, amount, notify, status=None):
            st = status or ledger.tx_status(height)
            confs = ledger.confirmations(height, tip)
            event(conn, wid, severity, kind, message, txid, st, confs)
            if notify:
                notifier.enqueue(conn, wallet, severity, kind, message, txid, st, confs, amount)
            log.log(logging.CRITICAL if severity == "critical" else logging.INFO,
                    "%s: %s %s txid=%s status=%s confirmations=%d", wallet["name"], kind, message, txid, st, confs)

        for ch in changes:
            out, row = ch.output, ch.previous
            if ch.kind == "receive":
                conn.execute(
                    "INSERT OR REPLACE INTO utxos(wallet_id,tx_hash,tx_pos,value,height,scripthash,first_seen,"
                    "spent_at,spend_txid,spend_height,spend_confirm_notified) VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL,0)",
                    (wid, out.txid, out.vout, out.value, out.height, out.scripthash, now),
                )
                if ch.notify:
                    emit("info", "receive", "New UTXO: %s to %s" % (_btc(out.value), own.get(out.scripthash, "?")),
                         out.txid, out.height, out.value, True)
            elif ch.kind in ("spend", "spend_replaced", "spend_resolved"):
                confirmed_now = ledger.is_confirmed(out.spend_height)
                # A spend first seen already confirmed gets one alert, not two.
                notified = 1 if (confirmed_now and (ch.kind == "spend" or not ch.notify)) else 0
                conn.execute(
                    "UPDATE utxos SET spent_at=COALESCE(spent_at, ?), spend_txid=?, spend_height=?,"
                    " spend_confirm_notified=? WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (now, out.spend_txid, out.spend_height, notified, wid, out.txid, out.vout),
                )
                if ch.kind == "spend":
                    emit(alarm, "spend", "UTXO spent: %s from %s:%s" % (_btc(out.value), out.txid, out.vout),
                         out.spend_txid, out.spend_height, out.value, ch.notify)
                elif ch.kind == "spend_replaced":
                    emit(alarm, "spend_replaced",
                         "Spending transaction replaced (RBF/conflict): %s from %s:%s, previous spend %s" % (
                             _btc(out.value), out.txid, out.vout, row["spend_txid"]),
                         out.spend_txid, out.spend_height, out.value, ch.notify)
            elif ch.kind == "spend_confirmed":
                conn.execute(
                    "UPDATE utxos SET spend_height=?, spend_confirm_notified=1 WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (out.spend_height, wid, out.txid, out.vout),
                )
                emit(alarm, "spend_confirmed", "Spending transaction confirmed: %s from %s:%s" % (
                    _btc(out.value), out.txid, out.vout), out.spend_txid, out.spend_height, out.value, ch.notify)
            elif ch.kind == "spend_unconfirmed":
                conn.execute(
                    "UPDATE utxos SET spend_height=?, spend_confirm_notified=0 WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (out.spend_height, wid, out.txid, out.vout),
                )
            elif ch.kind == "spend_reverted":
                conn.execute(
                    "UPDATE utxos SET spent_at=NULL, spend_txid=NULL, spend_height=NULL, spend_confirm_notified=0"
                    " WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (wid, out.txid, out.vout),
                )
                emit("warning", "spend_reverted",
                     "Spending transaction %s disappeared (dropped or replaced); UTXO %s:%s is unspent again: %s" % (
                         row["spend_txid"] or "?", out.txid, out.vout, _btc(out.value)),
                     row["spend_txid"], 0, out.value, ch.notify, status="dropped")
            elif ch.kind == "height":
                conn.execute(
                    "UPDATE utxos SET height=? WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (out.height, wid, out.txid, out.vout),
                )
            elif ch.kind == "receive_dropped":
                conn.execute(
                    "DELETE FROM utxos WHERE wallet_id=? AND tx_hash=? AND tx_pos=?",
                    (wid, row["tx_hash"], row["tx_pos"]),
                )
                emit("warning", "receive_dropped",
                     "Incoming transaction disappeared (dropped, replaced or reorganised): %s at %s:%s" % (
                         _btc(row["value"]), row["tx_hash"], row["tx_pos"]),
                     row["tx_hash"], 0, row["value"], ch.notify, status="dropped")

        self._refresh_events(conn, wid, tx_heights)
        if baseline:
            unspent = [o for o in truth.values() if not o.spent]
            total = sum(o.value for o in unspent)
            event(conn, wid, "info", "sync",
                  "Initial sync complete: %d unspent output(s), balance %s" % (len(unspent), _btc(total)),
                  None, "confirmed", 0)
            conn.execute("UPDATE wallets SET synced=1 WHERE id=?", (wid,))

    def _refresh_events(self, conn, wid, tx_heights):
        rows = conn.execute(
            "SELECT id, txid, status, confirmations FROM events WHERE wallet_id=? AND txid IS NOT NULL"
            " AND status != 'dropped'", (wid,)
        ).fetchall()
        for r in rows:
            if r["txid"] in tx_heights:
                h = tx_heights[r["txid"]]
                status, confs = ledger.tx_status(h), ledger.confirmations(h, self.tip_height)
            else:
                status, confs = "dropped", 0
            if status != r["status"] or confs != r["confirmations"]:
                conn.execute("UPDATE events SET status=?, confirmations=? WHERE id=?", (status, confs, r["id"]))
