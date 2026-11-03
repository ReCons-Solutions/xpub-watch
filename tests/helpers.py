"""Shared test helpers and BIP test vectors."""

# Account keys of the BIP39 test mnemonic "abandon ... about".
ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
YPUB = "ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7Ke7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP"
XPUB = "xpub6BosfCnifzxcFwrSzQiqu2DBVTshkCXacvNsWGYJVVhhawA7d4R5WSWGFNbi8Aw6ZRc1brxMyWMzG3DSSSSoekkudhUd9yLb6qx39T9nMdj"


def kinds(sent):
    return [k for k, _ in sent]


def events(dbmod, kind=None):
    with dbmod.connect() as conn:
        if kind:
            return conn.execute("SELECT * FROM events WHERE kind=? ORDER BY id", (kind,)).fetchall()
        return conn.execute("SELECT * FROM events ORDER BY id").fetchall()


def spk(xpub, index, branch=0, script_type="p2wpkh"):
    from wallet import derive_scriptpubkey

    return derive_scriptpubkey(xpub, branch, index, script_type)
