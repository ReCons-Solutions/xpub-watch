# Publishing checklist

## 1. Repository settings (once)

- Enable Dependabot alerts.
- Enable private vulnerability reporting / Security Advisories.
- Protect `main`; require the `CI` workflow to pass before merging.
- Allow GitHub Actions to create releases (Settings → Actions → Workflow permissions: read and write).

## 2. Prepare the version

```bash
scripts/set-version.sh 0.5.0-beta.1
# add/complete the "## [0.5.0-beta.1]" section in CHANGELOG.md (replace "unreleased" with the date)
python3 -m pytest
./scripts/validate-release.sh
git commit -am "Release 0.5.0-beta.1"
```

## 3. Field test on MyNode (before tagging)

Build a local archive and install it on a test node:

```bash
python3 scripts/build-release.py dist
```

MyNode → Marketplace / Community Apps → Add Application → upload `dist/xpubwatch-mynode-community-app-<version>.tar.gz`.

### Clean install

- [ ] Install succeeds; `journalctl -u xpubwatch -n 100 --no-pager` shows no errors
- [ ] MyNode tile appears, Open button works (HTTP 5115 redirects to HTTPS 5116)
- [ ] First visit shows "Set an access password"; password is set; logout/login works
- [ ] 6 wrong passwords → "Too many failed attempts"
- [ ] Electrum shows OK, tip height matches MyNode
- [ ] Add a zpub with "Automatic": first receive address in the flash message matches the wallet
- [ ] Add an xpub with "Automatic" → rejected; with explicit type → accepted
- [ ] Add the same key again → "already watched"
- [ ] Wallet with existing funds: balance correct, one `sync` event, **no** receive notifications

### Alerts (small test amounts)

- [ ] Receive → `receive` event + notification, status mempool → confirmed, confirmations increase
- [ ] Spend → `spend` (critical) + notification within one poll interval
- [ ] First confirmation of the spend → `spend_confirmed` notification, exactly once
- [ ] RBF fee bump of the spend → `spend_replaced`; confirmation tracked for the new txid
- [ ] DISARMED wallet with "Only notify for ARMED wallets" → events, no notifications
- [ ] Test notification shows a result per configured channel
- [ ] Stop electrs for 1 minute → UI shows the Electrum error; start it → recovers without restart and without duplicate alerts
- [ ] Notification channel unreachable during an event (e.g. wrong ntfy server) → fix it → the pending alert is delivered once

### Security

- [ ] Page source contains no notification token and no xpub
- [ ] Saving settings with empty token fields keeps the tokens; "Remove" deletes them
- [ ] `ls -l /mnt/hdd/mynode/xpubwatch` → owned by `bitcoin`, database `-rw-------`
- [ ] `sudo -u bitcoin ... manage.py reset-password` → setup page appears again

### Upgrade from 0.4.0-beta.1

- [ ] Back up `/mnt/hdd/mynode/xpubwatch`
- [ ] Install 0.5 over 0.4 with existing wallets and events
- [ ] Password setup appears; afterwards all wallets, balances and old events are present
- [ ] No notification burst after the upgrade (one `sync` event per wallet)
- [ ] `cat /home/bitcoin/.mynode/xpubwatch_version` shows the new version
- [ ] Spend from a wallet after the upgrade → alert as usual

## 4. Tag and publish

```bash
git tag v0.5.0-beta.1
git push origin main v0.5.0-beta.1
```

The `Release` workflow verifies that the tag matches `VERSION`, runs the tests and validation, builds the archive reproducibly and publishes a GitHub release with the archive and `SHA256SUMS`. Versions containing `-` are published as pre-release.

## 5. Test the published asset

Download the archive from the GitHub release, check it with `sha256sum -c SHA256SUMS`, and repeat the clean-install section on a node with the downloaded file (not a local build).

## 6. Submit to MyNode

After the beta has been tested by multiple users, open an issue in the MyNode repository proposing XPUB Watch for the official Marketplace. Include:

- repository URL
- current release URL
- screenshots
- security model
- supported MyNode versions / architectures tested
