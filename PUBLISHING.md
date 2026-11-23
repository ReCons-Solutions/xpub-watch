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

Copy `dist/xpubwatch-mynode-community-app-<version>.tar.gz` to the test node and install it with the bundled script (see README, "Installation on MyNode"):

```bash
scp dist/xpubwatch-mynode-community-app-<version>.tar.gz admin@<node-ip>:/tmp/
ssh admin@<node-ip> 'cd /tmp && rm -rf xpubwatch && tar xzf xpubwatch-mynode-community-app-*.tar.gz && sudo bash xpubwatch/install-on-mynode.sh'
```

### Clean install

- [ ] `install-on-mynode.sh` finishes with "installed and running"; `journalctl -u xpubwatch -n 100 --no-pager` shows no errors
- [ ] MyNode tile appears (and the app under Apps), Open button works (HTTP 5115 redirects to HTTPS 5116)
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
- [ ] Enable "Answer balance queries in Telegram": `/balance`, `/balance <name>`, `/status` answer within seconds; a message from another chat gets no answer; with the same bot used elsewhere (webhook) the UI shows the conflict
- [ ] Outage alarm: healthchecks.io check (period 5 min), ping URL saved → "test ping sent", check turns green; `sudo systemctl stop xpubwatch` → alert from healthchecks.io after period + grace; start again → check recovers
- [ ] `sudo chown root:root /mnt/hdd/mynode/xpubwatch && sudo systemctl restart xpubwatch` → service starts, folder owned by bitcoin again
- [ ] Stop electrs for 1 minute → UI shows the Electrum error; start it → recovers without restart and without duplicate alerts
- [ ] Notification channel unreachable during an event (e.g. wrong ntfy server) → fix it → the pending alert is delivered once

### Security

- [ ] Page source contains no notification token and no xpub
- [ ] Saving settings with empty token fields keeps the tokens; "Remove" deletes them
- [ ] `ls -l /mnt/hdd/mynode/xpubwatch` → owned by `bitcoin`, database `-rw-------`
- [ ] `sudo -u bitcoin ... manage.py reset-password` → setup page appears again

### Upgrade from 0.4.0-beta.1

- [ ] Back up `/mnt/hdd/mynode/xpubwatch`
- [ ] Install 0.5 over 0.4 with existing wallets and events; the script creates `/mnt/hdd/mynode/xpubwatch-backup-<date>`
- [ ] Running the script a second time (same version) reinstalls cleanly and reports the same wallet count before and after
- [ ] Uninstall under Apps works; the data folder is kept
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

Every build handed to a tester gets its own version (`scripts/set-version.sh 0.5.0-beta.2`): two different archives with the same version cannot be told apart on the node.

## 5. Test the published asset

Download the archive from the GitHub release, check it with `sha256sum -c SHA256SUMS`, and repeat the clean-install section on a node with the downloaded file (not a local build).

## 6. Path to 1.0.0

The first stable release is **1.0.0** (no 0.5.0 final).

| Step | Version | Gate |
|---|---|---|
| 1 | `0.5.0-beta.1` | field test §3 complete incl. a real small spend; PR #1 merged; tag |
| 2 | `0.5.0-beta.N` | 14 days continuous operation without an incident: no missed or duplicate alert, no crash, no unexplained DOWN on the outage monitor; fixes only |
| 3 | `1.0.0-rc.1` | installed as an upgrade with `deploy-to-mynode.sh`: wallet count unchanged; restore from a backup tried once |
| 4 | `1.0.0` | 7 days on the release candidate without a finding; no open bug affecting alerts or data |

For 1.0.0: README status "stable" (keep "not independently security-audited"), `CHANGELOG.md` section `[1.0.0]`, release notes with upgrade instructions.

## 7. Submit to MyNode

After the beta has been tested by multiple users, open an issue in the MyNode repository proposing XPUB Watch for the official Marketplace. Include:

- repository URL
- current release URL
- screenshots
- security model
- supported MyNode versions / architectures tested
