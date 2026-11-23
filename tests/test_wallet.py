"""Address derivation against the published BIP44/49/84 test vectors
(mnemonic "abandon abandon ... about")."""
import pytest

from helpers import XPUB, YPUB, ZPUB
from wallet import address_for, load_xpub

VECTORS = [
    # key, script_type, receive/0, change/0
    (ZPUB, "p2wpkh", "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu", "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"),
    (YPUB, "p2sh-p2wpkh", "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf", "34K56kSjgUCUSD8GTtuF7c9Zzwokbs6uZ7"),
    (XPUB, "p2pkh", "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA", "1J3J6EvPrv8q6AC3VCjWV45Uf3nssNMRtH"),
]


@pytest.mark.parametrize("key,script_type,receive0,change0", VECTORS)
def test_bip_vectors(key, script_type, receive0, change0):
    assert address_for(key, 0, 0, "main", script_type) == receive0
    assert address_for(key, 1, 0, "main", script_type) == change0


@pytest.mark.parametrize("key,purpose,script_type", [
    (ZPUB, 84, "p2wpkh"), (YPUB, 49, "p2sh-p2wpkh"), (XPUB, 44, "p2pkh"),
])
def test_independent_private_derivation_matches(key, purpose, script_type):
    """Cross-check public derivation against hardened derivation from the seed."""
    from embit import bip32, bip39, script

    build = {
        "p2wpkh": lambda pub: script.p2wpkh(pub),
        "p2sh-p2wpkh": lambda pub: script.p2sh(script.p2wpkh(pub)),
        "p2pkh": lambda pub: script.p2pkh(pub),
    }[script_type]
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed("abandon " * 11 + "about"))
    for branch in (0, 1):
        for i in range(3):
            priv = root.derive("m/%dh/0h/0h/%d/%d" % (purpose, branch, i))
            expected = build(priv.to_public().key).address()
            assert address_for(key, branch, i, "main", script_type) == expected


def test_private_key_rejected():
    from embit import bip32, bip39

    xprv = bip32.HDKey.from_seed(bip39.mnemonic_to_seed("abandon " * 11 + "about")).to_base58()
    with pytest.raises(ValueError):
        load_xpub(xprv)


@pytest.mark.parametrize("garbage", ["", "xpub123", "not a key", ZPUB[:-1] + "x"])
def test_garbage_rejected(garbage):
    with pytest.raises(Exception):
        load_xpub(garbage)
