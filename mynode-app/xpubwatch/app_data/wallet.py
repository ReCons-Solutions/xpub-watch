import hashlib
from embit import base58, bip32, networks, script

# SLIP-0132 public versions -> standard xpub/tpub versions.
SLIP132 = {
    bytes.fromhex("049d7cb2"): bytes.fromhex("0488b21e"),  # ypub -> xpub
    bytes.fromhex("04b24746"): bytes.fromhex("0488b21e"),  # zpub -> xpub
    bytes.fromhex("044a5262"): bytes.fromhex("043587cf"),  # upub -> tpub
    bytes.fromhex("045f1cf6"): bytes.fromhex("043587cf"),  # vpub -> tpub
}


def normalize_extended_pub(value: str) -> str:
    raw = base58.decode_check(value.strip())
    if len(raw) != 78:
        raise ValueError("Invalid extended public key length")
    version = raw[:4]
    if version in SLIP132:
        raw = SLIP132[version] + raw[4:]
        return base58.encode_check(raw)
    return value.strip()


def load_xpub(value: str):
    key = bip32.HDKey.from_base58(normalize_extended_pub(value))
    if key.is_private:
        raise ValueError("Private extended keys are not accepted")
    return key


def derive_scriptpubkey(xpub: str, branch: int, index: int, script_type: str = "p2wpkh"):
    if branch not in (0, 1) or index < 0:
        raise ValueError("Invalid derivation index")
    root = load_xpub(xpub)
    child = root.derive([branch, index])
    if script_type == "p2wpkh":
        return script.p2wpkh(child.key)
    if script_type == "p2pkh":
        return script.p2pkh(child.key)
    if script_type == "p2sh-p2wpkh":
        return script.p2sh(script.p2wpkh(child.key))
    raise ValueError("Unsupported script type")


def electrum_scripthash(script_pubkey) -> str:
    # Electrum protocol uses SHA256(scriptPubKey), byte-reversed.
    return hashlib.sha256(script_pubkey.data).digest()[::-1].hex()


def address_for(xpub: str, branch: int, index: int, network: str, script_type: str = "p2wpkh") -> str:
    net = networks.NETWORKS[network]
    return derive_scriptpubkey(xpub, branch, index, script_type).address(net)


def derive_address_record(xpub: str, branch: int, index: int, network: str, script_type: str):
    spk = derive_scriptpubkey(xpub, branch, index, script_type)
    return {
        "branch": branch,
        "idx": index,
        "address": spk.address(networks.NETWORKS[network]),
        "scripthash": electrum_scripthash(spk),
    }
