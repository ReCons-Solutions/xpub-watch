# Contributing

## Scope

XPUB Watch should remain a small, auditable, watch-only Bitcoin monitoring application for MyNode.

## Principles

1. Never require seed phrases or private keys.
2. Prefer local node data over external blockchain APIs.
3. Treat xpubs and notification credentials as sensitive.
4. Keep dependencies minimal.
5. Preserve upgrade compatibility for `/mnt/hdd/mynode/xpubwatch`.
6. Add migrations for persistent database schema changes.
7. Avoid silent behavior changes in alert semantics.

## Development flow

1. Fork or branch from the current release.
2. Make one focused change.
3. Run:

```bash
./scripts/validate-release.sh
```

4. Test a clean MyNode Community App installation.
5. Test upgrade from the previous version with existing data.
6. Verify receive, spend and confirmation events.
7. Verify Telegram test notification when notification code changes.

## Pull requests

Include:

- problem statement
- implementation summary
- security / privacy impact
- upgrade / migration impact
- manual test results

Never include real xpubs, bot tokens, seeds, private keys or unredacted production databases.
