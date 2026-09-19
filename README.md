# XPUB Watch

![XPUB Watch logo](docs/assets/xpubwatch-logo.png)

XPUB Watch is a local, watch-only Bitcoin monitoring application for MyNode. It monitors one or more account-level `xpub` / `ypub` / `zpub` keys through the Electrum Server running on the same MyNode and can alert when funds are received or a known UTXO is spent.

> **Never enter a seed phrase, private key, hardware-wallet PIN, or BIP39 passphrase into XPUB Watch.**

## Status

**v0.4.0-beta.1** — public beta candidate based on the tested 0.3.9 MyNode build.

The core receive, spend, mempool, confirmation-tracking, multi-wallet, MyNode Community App installation and Telegram notification flows have been tested on a MyNode installation. See [Known limitations](#known-limitations) before exposing the web UI beyond a trusted local network.

## Features

- Multiple watch-only wallets
- `xpub`, `ypub`, `zpub` account keys
- BIP44 legacy (`1...`)
- BIP49 nested SegWit (`3...`)
- BIP84 native SegWit (`bc1q...`)
- Receive and change address discovery
- Dynamic look-ahead scanning
- UTXO tracking
- Receive events
- Spend detection from the mempool
- Spending transaction ID resolution
- Confirmation counting
- ARMED / DISARMED state per wallet
- Telegram notifications
- Optional ntfy / Gotify notifications
- Configurable receive / spend / confirmation alerts
- Optional "ARMED wallets only" notifications
- Local SQLite persistence
- Local Electrum Server usage — no public blockchain API required
- MyNode Community App package

## Architecture

```text
xpub / ypub / zpub
        │
        ▼
   XPUB Watch
        │
        ├── BIP32 address derivation
        ├── receive/change discovery
        ├── UTXO state
        ├── event engine
        └── notifications
        │
        ▼
127.0.0.1:50001
 MyNode Electrum Server
        │
        ▼
   Bitcoin Core
```

XPUB Watch never needs signing keys. The extended public key is sensitive privacy data, however, because it can reveal the transaction history and derived addresses of the corresponding account.

## Screenshots

### MyNode integration

![MyNode dashboard](docs/screenshots/mynode-dashboard.png)

### Web interface

![XPUB Watch web UI](docs/screenshots/web-ui.png)

## Requirements

- MyNode with dynamic / Community App support
- Bitcoin Core synced
- Electrum Server (`electrs`) installed and running
- Network access from MyNode for optional Telegram / ntfy / Gotify notifications

## Installation on MyNode

Download the Community App archive from the GitHub Release and install it through:

```text
MyNode → Marketplace / Community Apps → Add Application
```

Upload:

```text
xpubwatch-mynode-community-app-0.4.0-beta.1.tar.gz
```

After installation open XPUB Watch from the MyNode dashboard.

Persistent application data is stored at:

```text
/mnt/hdd/mynode/xpubwatch
```

Application code is installed at:

```text
/opt/mynode/xpubwatch
```

## Adding a wallet

Use an **account-level extended public key**, not a seed.

Select the matching script type:

| Key / wallet type | XPUB Watch script type | Typical address |
|---|---|---|
| BIP84 | Native SegWit | `bc1q...` |
| BIP49 | Nested SegWit | `3...` |
| BIP44 | Legacy | `1...` |

After adding a wallet, verify several derived receive addresses against the source wallet before relying on alerts.

## Telegram setup

1. Create a bot with Telegram `@BotFather`.
2. Start the bot and send it a message.
3. Obtain the chat ID using the Telegram Bot API.
4. Enter the bot token and chat ID in XPUB Watch.
5. Use **Send test notification**.

The bot token is stored locally in the XPUB Watch SQLite database. It is not embedded in the source code or release archive.

Example spend notification:

```text
🚨 XPUB Watch — SPEND

Wallet: Coldcard Main
Amount: 5,000 sat (0.00005000 BTC)
Status: MEMPOOL / unconfirmed
Confirmations: 0
TXID: …
Mempool: https://mempool.space/tx/…
```

## Security model

XPUB Watch is watch-only. A compromise of XPUB Watch must not provide signing capability because the application does not require private keys.

However, a compromise can expose:

- account xpub / ypub / zpub values
- derived addresses
- balances and transaction history
- wallet labels
- notification configuration and tokens stored locally

Read [SECURITY.md](SECURITY.md) before deployment.

## Privacy model

Blockchain queries are sent to the Electrum Server on the same MyNode by default:

```text
127.0.0.1:50001
```

XPUB Watch does not submit xpubs to public blockchain APIs.

Optional notification channels necessarily disclose alert metadata to their service provider. Telegram alerts can include wallet label, amount, status, TXID and a transaction link.

## Backup

Before upgrades or major changes, back up:

```text
/mnt/hdd/mynode/xpubwatch/xpubwatch.db
/mnt/hdd/mynode/xpubwatch/flask_secret
```

Stop the service first for a consistent SQLite backup:

```bash
sudo systemctl stop xpubwatch
sudo cp -a /mnt/hdd/mynode/xpubwatch /path/to/secure/backup/
sudo systemctl start xpubwatch
```

Treat the backup as sensitive because it can contain xpubs and notification credentials.

## Upgrade

Upload the newer Community App archive through MyNode. The storage directory under `/mnt/hdd/mynode/xpubwatch` is preserved by normal upgrades.

Always verify after an upgrade:

```bash
cat /home/bitcoin/.mynode/xpubwatch_version
systemctl status xpubwatch --no-pager
```

## Uninstall

Use the MyNode application management UI. The exact data-retention behavior should be checked before uninstalling; back up `/mnt/hdd/mynode/xpubwatch` first if the data must be retained.

## Troubleshooting

### Electrum timeout

Check the local port:

```bash
sudo ss -ltnp | grep 50001
```

Direct test:

```bash
cd /opt/mynode/xpubwatch
sudo -u bitcoin .venv/bin/python
```

```python
from electrum_client import ElectrumClient
c = ElectrumClient("127.0.0.1", 50001)
print(c.tip())
```

### Service logs

```bash
journalctl -u xpubwatch -n 100 --no-pager
```

## Known limitations

- Public beta; not independently security-audited.
- Web UI is intended for a trusted local MyNode environment. Do not expose the XPUB Watch ports directly to the public Internet.
- Notification secrets are stored locally in SQLite. Protect MyNode backups and filesystem access.
- Taproot / BIP86 account monitoring is not yet implemented.
- XPUB Watch detects unauthorized-looking spends but cannot prevent a valid transaction signed with compromised private keys.
- A mempool alarm does not guarantee there is enough time to rescue funds.

## Development

The MyNode Community App source is in:

```text
mynode-app/xpubwatch/
```

Run the local release checks:

```bash
./scripts/validate-release.sh
```

## License

MIT. See [LICENSE](LICENSE).
