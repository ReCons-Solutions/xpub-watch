# Changelog

All notable changes to XPUB Watch are documented here.

## [0.5.0-beta.1] - unreleased

Hardening release. Upgrading from 0.4.0-beta.1 keeps all wallets, events and
settings; see "Upgrading from 0.4" in the README.

### Security
- Web UI now requires a password (set on first visit after install/upgrade).
- CSRF protection on all forms, login throttling, hardened session cookie
  (own cookie name, HttpOnly, Secure, SameSite=Strict).
- Notification tokens are write-only and never rendered back into the page.
- Content-Security-Policy and further security headers; no inline JavaScript.
- Gotify token is sent in a header instead of the URL.
- Exactly one background watcher per data directory (file lock).
- Database and data directory restricted to the service user.

### Fixed
- Duplicate SPEND alerts when a later part of a scan failed (rollback after
  the notification had been sent).
- A dropped or replaced spend re-announced the UTXO as a new receive.
- A dropped unconfirmed incoming transaction raised a critical SPEND alarm.
- Outputs received and spent between two polls were never reported.
- RBF replacement of a spend was not tracked; confirmation tracking stuck on
  the replaced transaction.
- Adding a funded wallet sent one receive alert per existing UTXO.
- Gap-limit discovery needed several poll cycles.

### Changed
- Wallet state is derived from the Electrum transaction history (ledger)
  instead of diffing `listunspent` snapshots. New event types:
  `spend_replaced`, `spend_reverted`, `receive_dropped`, `sync`.
- Notifications use a transactional outbox with per-channel retry.
- Persistent, pipelined Electrum connection; histories are only fetched when
  an address's Electrum status hash changes (about 10x faster idle scans).
- Raw transactions are parsed locally; verbose `getrawtransaction` / txindex
  is no longer required.
- Network and script type are derived from the key prefix; conflicting
  selections and duplicates are rejected; multisig keys are rejected.
- Test notification reports the result per channel.
- Version is defined once (`app_data/VERSION`); release archives are built
  reproducibly by CI on tags instead of being committed.

### Added
- Automated test suite (BIP vectors, watcher scenarios, Electrum protocol,
  web security) running on Python 3.8 and 3.11.
- `manage.py reset-password`.
- Unconfirmed balance, "acknowledge all", readable timestamps.

## [0.4.0-beta.1] - 2026-09-19

First publication candidate based on the field-tested 0.3.9 MyNode build.

### Added
- Public repository structure and documentation.
- MIT license.
- Security policy.
- Release validation script and CI workflow.
- MyNode Community App release archive.

### Core functionality included
- Multi-wallet xpub / ypub / zpub monitoring.
- BIP44, BIP49 and BIP84 address derivation.
- Receive and change address discovery.
- UTXO tracking.
- Receive and spend events.
- Mempool spend detection.
- Spending transaction ID resolution.
- Confirmation tracking after received UTXOs are later spent.
- ARMED / DISARMED wallet state.
- Telegram, ntfy and Gotify notifications.
- Configurable receive, spend and confirmation alerts.
- Optional ARMED-only alerts.
- MyNode Community App installation.
- XPUB Watch branding in the MyNode tile and web interface.
- Resilient Electrum RPC retries and timeout handling.

## 0.3.9
- Increased Electrum RPC resilience with retry logic and longer timeouts.

## 0.3.8
- Improved MyNode Open-button routing using HTTP-to-HTTPS redirection.

## 0.3.7
- Community App archive suitable for MyNode Add Application workflow.

## 0.3.6
- Cleaner Telegram messages with sat/BTC amount, TXID and explorer link.
- Configurable alert types and ARMED-only filtering.

## 0.3.5
- Larger branding assets and removal of beta ribbon from the MyNode tile.

## 0.3.4
- XPUB Watch logo integrated into MyNode tile and web UI.

## 0.3.3
- Receive confirmation counts continue updating after the UTXO is spent.

## 0.3.2
- Spending TXID, mempool/confirmed status, confirmation tracking and explorer links.

## 0.3.1
- Telegram notifications.

## 0.3.0
- Multi-wallet management, derived-address display, Gunicorn and notification framework.
