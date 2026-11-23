"""Transaction ledger: derives the wallet's true UTXO state from its history.

Pure functions only. The watcher feeds them the wallet's Electrum history and
the parsed transactions; the result is compared with the persisted UTXO table
to produce events. Recomputing from history (instead of diffing listunspent)
makes dropped, replaced and reorganised transactions visible, and catches
outputs that were received and spent between two polls.
"""


def is_confirmed(height):
    return int(height or 0) > 0


def tx_status(height):
    return "confirmed" if is_confirmed(height) else "mempool"


def confirmations(height, tip_height):
    h = int(height or 0)
    tip = int(tip_height or 0)
    if h <= 0 or tip <= 0 or h > tip:
        return 0
    return tip - h + 1


class Output(object):
    __slots__ = ("txid", "vout", "scripthash", "value", "height", "spend_txid", "spend_height")

    def __init__(self, txid, vout, scripthash, value, height):
        self.txid = txid
        self.vout = vout
        self.scripthash = scripthash
        self.value = value
        self.height = height
        self.spend_txid = None
        self.spend_height = None

    @property
    def outpoint(self):
        return (self.txid, self.vout)

    @property
    def spent(self):
        return self.spend_txid is not None


def _better_spender(current, candidate):
    """Pick the spender to report when several txs claim one outpoint.

    Electrum servers drop conflicting mempool txs, so this only matters in
    transient windows. Confirmed beats unconfirmed; then the higher block;
    ties break deterministically by txid.
    """
    if current is None:
        return candidate
    cur_conf, cand_conf = is_confirmed(current[1]), is_confirmed(candidate[1])
    if cur_conf != cand_conf:
        return candidate if cand_conf else current
    if cur_conf and current[1] != candidate[1]:
        return candidate if candidate[1] < current[1] else current
    return min(current, candidate, key=lambda s: s[0])


def compute(tx_heights, parsed, own_scripthashes):
    """Return {outpoint: Output} for every output paying one of our scripts.

    tx_heights:       {txid: height} of all txs in the wallet's history
    parsed:           {txid: {"in": [[txid, vout]], "out": [[vout, sh, value]]}}
    own_scripthashes: set of scripthashes belonging to the wallet
    """
    outputs = {}
    for txid, height in tx_heights.items():
        tx = parsed.get(txid)
        if tx is None:
            continue
        for vout, sh, value in tx["out"]:
            if sh in own_scripthashes:
                outputs[(txid, int(vout))] = Output(txid, int(vout), sh, int(value), int(height or 0))

    spenders = {}
    for txid, height in tx_heights.items():
        tx = parsed.get(txid)
        if tx is None:
            continue
        for prev_txid, prev_vout in tx["in"]:
            op = (prev_txid, int(prev_vout))
            if op in outputs:
                spenders[op] = _better_spender(spenders.get(op), (txid, int(height or 0)))

    for op, (txid, height) in spenders.items():
        outputs[op].spend_txid = txid
        outputs[op].spend_height = height
    return outputs


class Change(object):
    """One state transition detected by reconcile()."""

    __slots__ = ("kind", "output", "previous", "notify")

    def __init__(self, kind, output=None, previous=None, notify=True):
        self.kind = kind
        self.output = output
        self.previous = previous
        self.notify = notify


def reconcile(truth, stored, baseline):
    """Compare computed truth with stored UTXO rows.

    truth:    {outpoint: Output}
    stored:   {outpoint: row-like mapping with value, height, spent_at,
              spend_txid, spend_height, spend_confirm_notified}
    baseline: True while the wallet has not completed its first sync.
              Outputs first discovered during the baseline and corrections
              of stale stored state are recorded without alerts; a spend or
              replaced spend of an already stored UTXO still alerts.

    Returns a list of Change objects in a stable, causal order.
    """
    changes = []
    for op in sorted(truth, key=lambda o: (not is_confirmed(truth[o].height), truth[o].height, o)):
        out = truth[op]
        row = stored.get(op)
        if row is None:
            changes.append(Change("receive", out, None, notify=not baseline))
            if out.spent:
                kind = "spend"
                changes.append(Change(kind, out, None, notify=not baseline))
            continue

        was_spent = row["spend_txid"] is not None or row["spent_at"] is not None
        if not was_spent and out.spent:
            changes.append(Change("spend", out, row))
        elif was_spent and not out.spent:
            # During the one-time re-baseline after an upgrade this corrects
            # stale state (e.g. a false 0.4 spend alarm); record it silently.
            changes.append(Change("spend_reverted", out, row, notify=not baseline))
        elif was_spent and out.spent:
            if row["spend_txid"] is None:
                # Legacy row ("detected" without spending txid): fill in silently.
                changes.append(Change("spend_resolved", out, row, notify=False))
            elif row["spend_txid"] != out.spend_txid:
                changes.append(Change("spend_replaced", out, row))
            elif is_confirmed(out.spend_height) and not int(row["spend_confirm_notified"] or 0):
                changes.append(Change("spend_confirmed", out, row))
            elif not is_confirmed(out.spend_height) and int(row["spend_confirm_notified"] or 0):
                # Spend confirmation undone by a reorg: re-arm the confirmation alert.
                changes.append(Change("spend_unconfirmed", out, row, notify=False))
        if int(row["height"] or 0) != out.height:
            changes.append(Change("height", out, row, notify=False))

    for op, row in stored.items():
        if op not in truth:
            changes.append(Change("receive_dropped", None, row, notify=not baseline))
    return changes
