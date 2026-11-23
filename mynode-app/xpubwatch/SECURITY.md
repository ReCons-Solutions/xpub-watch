# Security Policy

## Supported release

Security fixes are intended for the latest published XPUB Watch release.

## Security boundaries

XPUB Watch is deliberately watch-only. It must never request or store:

- BIP39 seed phrases
- raw private keys
- hardware-wallet PINs
- BIP39 passphrases

An `xpub` / `ypub` / `zpub` is still sensitive. Possession of an account extended public key can expose the derived addresses, balances and transaction history of that account.

## Network exposure

The application is designed for a trusted local MyNode environment. Do **not** expose XPUB Watch ports directly to the public Internet.

The application queries the local MyNode Electrum Server at `127.0.0.1:50001` by default.

## Notification credentials

Telegram, ntfy and Gotify credentials are stored in the local SQLite database. Protect:

```text
/mnt/hdd/mynode/xpubwatch
```

and any backups of that directory.

The web UI should be treated as sensitive administrative access. Anyone able to administer the UI can change wallets, alert settings and notification destinations.

## Web UI protection

- Password authentication (PBKDF2-SHA256, 600,000 iterations). The password is set on the first visit after installation; set it immediately.
- CSRF token on every state-changing request; failed logins are throttled with exponential backoff.
- Session cookie `xpubwatch_session`: `HttpOnly`, `Secure`, `SameSite=Strict`, 12 hour lifetime.
- Content-Security-Policy without inline scripts, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- Notification tokens are write-only in the UI. Extended public keys are never displayed after they were added.
- The application binds to `127.0.0.1` only and is reached through MyNode's nginx over HTTPS.

## Reporting a vulnerability

Use a **private GitHub Security Advisory** (Security → Report a vulnerability) for vulnerabilities that could expose wallet metadata, notification credentials, permit unauthorized administrative actions, or materially weaken spend detection.

Do not include real xpubs, bot tokens, private keys, seeds or other secrets in public issues.

## Disclosure expectations

Please provide:

- affected version
- MyNode version and architecture
- reproduction steps
- expected versus actual behavior
- relevant sanitized logs

Allow reasonable time for validation and a fix before public disclosure.
