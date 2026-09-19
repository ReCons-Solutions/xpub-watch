# Changelog

All notable changes to XPUB Watch are documented here.

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
