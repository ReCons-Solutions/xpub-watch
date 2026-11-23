import hashlib
from functools import lru_cache

from embit import base58, bip32, networks, script
from embit.transaction import Transaction

SCRIPT_TYPES = ("p2wpkh", "p2sh-p2wpkh", "p2pkh")

# Public version bytes -> (standard version, network, implied script type).
# Script type None means the prefix does not define it (xpub / tpub).
VERSIONS = {
    bytes.fromhex("0488b21e"): (bytes.fromhex("0488b21e"), "main", None),           # xpub
    bytes.fromhex("049d7cb2"): (bytes.fromhex("0488b21e"), "main", "p2sh-p2wpkh"),  # ypub
    bytes.fromhex("04b24746"): (bytes.fromhex("0488b21e"), "main", "p2wpkh"),       # zpub
    bytes.fromhex("043587cf"): (bytes.fromhex("043587cf"), "test", None),           # tpub
    bytes.fromhex("044a5262"): (bytes.fromhex("043587cf"), "test", "p2sh-p2wpkh"),  # upub
    bytes.fromhex("045f1cf6"): (bytes.fromhex("043587cf"), "test", "p2wpkh"),       # vpub
}
# SLIP-0132 multisig prefixes: not supported by a single-key watcher.
MULTISIG_VERSIONS = {
    bytes.fromhex("0295b43f"), bytes.fromhex("02aa7ed3"),  # Ypub, Zpub
    bytes.fromhex("024289ef"), bytes.fromhex("02575483"),  # Upub, Vpub
}
PRIVATE_VERSIONS = {
    bytes.fromhex(v) for v in (
        "0488ade4", "049d7878", "04b2430c", "04358394", "044a4e28", "045f18bc",
        "0295b005", "02aa7a99", "024285b5", "02575048",
    )
}


class KeyInfo(object):
    __slots__ = ("normalized", "network", "implied_script_type", "depth")

    def __init__(self, normalized, network, implied_script_type, depth):
        self.normalized = normalized
        self.network = network
        self.implied_script_type = implied_script_type
        self.depth = depth


def inspect_key(value):
    """Validate an extended public key and describe it.

    Raises ValueError with a user-facing message for anything that is not a
    supported single-signature account public key.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("Extended public key is required")
    try:
        raw = base58.decode_check(value)
    except Exception:
        raise ValueError("Not a valid extended public key (checksum or encoding error)")
    if len(raw) != 78:
        raise ValueError("Invalid extended public key length")
    version = raw[:4]
    if version in PRIVATE_VERSIONS:
        raise ValueError("Private extended keys are not accepted. Use the account xpub/ypub/zpub only")
    if version in MULTISIG_VERSIONS:
        raise ValueError("Multisig keys (Ypub/Zpub) are not supported")
    if version not in VERSIONS:
        raise ValueError("Unknown extended key version")
    standard, network, implied = VERSIONS[version]
    normalized = base58.encode_check(standard + raw[4:])
    key = bip32.HDKey.from_base58(normalized)
    if key.is_private:
        raise ValueError("Private extended keys are not accepted")
    return KeyInfo(normalized, network, implied, raw[4])


def normalize_extended_pub(value):
    return inspect_key(value).normalized


@lru_cache(maxsize=64)
def _load_normalized(normalized):
    return bip32.HDKey.from_base58(normalized)


def load_xpub(value):
    return _load_normalized(normalize_extended_pub(value))


@lru_cache(maxsize=128)
def _branch_key(normalized, branch):
    return _load_normalized(normalized).derive([branch])


def derive_scriptpubkey(xpub, branch, index, script_type="p2wpkh"):
    if branch not in (0, 1) or index < 0:
        raise ValueError("Invalid derivation index")
    child = _branch_key(normalize_extended_pub(xpub), branch).derive([index])
    if script_type == "p2wpkh":
        return script.p2wpkh(child.key)
    if script_type == "p2pkh":
        return script.p2pkh(child.key)
    if script_type == "p2sh-p2wpkh":
        return script.p2sh(script.p2wpkh(child.key))
    raise ValueError("Unsupported script type")


def electrum_scripthash(script_pubkey):
    # Electrum protocol uses SHA256(scriptPubKey), byte-reversed.
    data = script_pubkey.data if hasattr(script_pubkey, "data") else script_pubkey
    return hashlib.sha256(data).digest()[::-1].hex()


def address_for(xpub, branch, index, network, script_type="p2wpkh"):
    return derive_scriptpubkey(xpub, branch, index, script_type).address(networks.NETWORKS[network])


def derive_address_record(xpub, branch, index, network, script_type):
    spk = derive_scriptpubkey(xpub, branch, index, script_type)
    return {
        "branch": branch,
        "idx": index,
        "address": spk.address(networks.NETWORKS[network]),
        "scripthash": electrum_scripthash(spk),
    }


def parse_raw_tx(raw_hex):
    """Reduce a raw transaction to what the ledger needs.

    Returns {"in": [[prev_txid, vout], ...], "out": [[vout, scripthash, value], ...]}.
    """
    tx = Transaction.from_string(raw_hex)
    return {
        "in": [[vin.txid.hex(), int(vin.vout)] for vin in tx.vin],
        "out": [[i, electrum_scripthash(o.script_pubkey), int(o.value)] for i, o in enumerate(tx.vout)],
    }
