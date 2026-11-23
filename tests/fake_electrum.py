"""In-memory Electrum backend for tests.

Models a tiny chain: transactions with a height (0 = mempool). Histories,
UTXO sets and Electrum status hashes are derived from that set exactly like an
Electrum server would derive them, so the watcher can be exercised end to end
without electrs or bitcoind.
"""
import hashlib
import itertools
import os

from embit import script as escript
from embit.transaction import Transaction, TransactionInput, TransactionOutput

_nonce = itertools.count(1)


def scripthash_of(script_pubkey):
    return hashlib.sha256(script_pubkey.data).digest()[::-1].hex()


def external_outpoint():
    """A random outpoint that does not belong to any watched wallet."""
    return (os.urandom(32).hex(), 0)


def build_tx(inputs, outputs):
    """inputs: [(txid_hex, vout)], outputs: [(Script, value_sat)]."""
    vin = [TransactionInput(bytes.fromhex(txid), vout) for txid, vout in inputs]
    vout = [TransactionOutput(value, spk) for spk, value in outputs]
    # A unique locktime keeps txids unique even for otherwise identical txs.
    return Transaction(version=2, vin=vin, vout=vout, locktime=next(_nonce))


class ElectrumDown(OSError):
    pass


class FakeElectrum:
    def __init__(self, tip=800_000):
        self.tip_height = tip
        self.txs = {}  # txid -> (Transaction, height)
        self.fail_after = None  # raise after N more calls (fault injection)
        self.fail_scripthashes = set()  # raise when any of these is queried
        self.calls = 0

    # ---- chain manipulation -------------------------------------------------
    def add_tx(self, tx, height=0):
        txid = tx.txid().hex()
        self.txs[txid] = (tx, height)
        return txid

    def pay(self, script_pubkey, value, height=0):
        """External payment to a script. Returns (txid, vout)."""
        tx = build_tx([external_outpoint()], [(script_pubkey, value)])
        return self.add_tx(tx, height), 0

    def spend(self, outpoints, dest=None, value=None, height=0):
        """Spend outpoints to an external script (or dest)."""
        total = sum(self._output(op).value for op in outpoints)
        dest = dest or escript.Script(b"\x00\x14" + os.urandom(20))
        tx = build_tx(outpoints, [(dest, value if value is not None else total - 500)])
        return self.add_tx(tx, height)

    def confirm(self, txid, height=None):
        tx, _ = self.txs[txid]
        self.txs[txid] = (tx, height if height is not None else self.tip_height + 1)
        self.tip_height = max(self.tip_height, self.txs[txid][1])

    def drop(self, txid):
        self.txs.pop(txid)

    def mine(self, blocks=1):
        self.tip_height += blocks

    def _output(self, outpoint):
        txid, vout = outpoint
        return self.txs[txid][0].vout[vout]

    # ---- derived views ------------------------------------------------------
    def _spent(self):
        spent = {}
        for txid, (tx, _) in self.txs.items():
            for vin in tx.vin:
                spent[(vin.txid.hex(), vin.vout)] = txid
        return spent

    def _history(self, scripthash):
        hits = []
        for txid, (tx, height) in self.txs.items():
            touches = any(scripthash_of(o.script_pubkey) == scripthash for o in tx.vout)
            if not touches:
                for vin in tx.vin:
                    prev = self.txs.get(vin.txid.hex())
                    if prev and scripthash_of(prev[0].vout[vin.vout].script_pubkey) == scripthash:
                        touches = True
                        break
            if touches:
                hits.append({"tx_hash": txid, "height": height})
        confirmed = sorted((h for h in hits if h["height"] > 0), key=lambda h: (h["height"], h["tx_hash"]))
        mempool = sorted((h for h in hits if h["height"] <= 0), key=lambda h: h["tx_hash"])
        return confirmed + mempool

    def _tick(self, scripthashes=()):
        self.calls += 1
        if self.fail_scripthashes.intersection(scripthashes):
            raise ElectrumDown("injected electrum failure for scripthash")
        if self.fail_after is not None:
            if self.fail_after <= 0:
                raise ElectrumDown("injected electrum failure")
            self.fail_after -= 1

    # ---- client API (old single-call style) ---------------------------------
    def version(self):
        self._tick()
        return ["fake", "1.4"]

    def tip(self):
        self._tick()
        return {"height": self.tip_height}

    def history(self, scripthash):
        self._tick([scripthash])
        return self._history(scripthash)

    def listunspent(self, scripthash):
        self._tick([scripthash])
        spent = self._spent()
        out = []
        for txid, (tx, height) in self.txs.items():
            for i, o in enumerate(tx.vout):
                if scripthash_of(o.script_pubkey) == scripthash and (txid, i) not in spent:
                    out.append({"tx_hash": txid, "tx_pos": i, "value": o.value, "height": height})
        return out

    def transaction(self, txid, verbose=False):
        self._tick()
        tx, height = self.txs[txid]
        if not verbose:
            return tx.to_string()
        return {
            "txid": txid,
            "hex": tx.to_string(),
            "vin": [{"txid": v.txid.hex(), "vout": v.vout} for v in tx.vin],
        }

    # ---- client API (batched style) -----------------------------------------
    def status(self, scripthash):
        hist = self._history(scripthash)
        if not hist:
            return None
        s = "".join("%s:%d:" % (h["tx_hash"], h["height"]) for h in hist)
        return hashlib.sha256(s.encode()).hexdigest()

    def subscribe_many(self, scripthashes):
        self._tick(scripthashes)
        return [self.status(sh) for sh in scripthashes]

    def history_many(self, scripthashes):
        self._tick(scripthashes)
        return [self._history(sh) for sh in scripthashes]

    def transactions_raw(self, txids):
        self._tick()
        return [self.txs[t][0].to_string() for t in txids]

    def close(self):
        pass
