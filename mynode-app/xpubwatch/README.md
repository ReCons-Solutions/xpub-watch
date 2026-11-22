# XPUB Watch for MyNode

XPUB Watch is a local, watch-only Bitcoin monitor for multiple `xpub` / `ypub` / `zpub` accounts.

This directory is the MyNode app folder. It is installed to `/usr/share/mynode_apps/xpubwatch` and registered with `mynode-manage-apps`:

```bash
sudo bash xpubwatch/install-on-mynode.sh
```

Security: never enter a seed phrase, private key, hardware-wallet PIN, or BIP39 passphrase.

Persistent data is stored under `/mnt/hdd/mynode/xpubwatch`.

After installation open the app and set the access password immediately.

See the repository-level `README.md` and `SECURITY.md` for full documentation.
