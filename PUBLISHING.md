# Publishing checklist

## 1. Create the public repository

Suggested repository name:

```text
xpub-watch
```

## 2. Push this repository

```bash
git init
git add .
git commit -m "XPUB Watch v0.4.0-beta.1"
git branch -M main
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

## 3. Enable GitHub security features

- Enable Dependabot alerts if available.
- Enable private vulnerability reporting / Security Advisories.
- Protect the `main` branch if multiple maintainers are involved.

## 4. Create the release

Suggested Git tag:

```text
v0.4.0-beta.1
```

Mark the GitHub release as **Pre-release**.

Attach files from `release/`:

```text
xpubwatch-mynode-community-app-0.4.0-beta.1.tar.gz
SHA256SUMS
```

## 5. Release notes

Use the `0.4.0-beta.1` section from `CHANGELOG.md` and add a prominent beta warning.

## 6. Test the published asset

On a clean MyNode:

```text
Marketplace / Community Apps → Add Application
```

Upload the exact archive downloaded from the GitHub Release, not a local build.

Verify:

- app install succeeds
- MyNode tile appears
- Open button works
- Electrum reports OK
- known test xpub derives the expected addresses
- receive alert works
- spend alert works
- confirmations increase
- Telegram test and live alerts work
- upgrade does not remove persistent data

## 7. Submit to MyNode

After the beta has been tested by multiple users, open an issue in the MyNode repository proposing XPUB Watch for the official Marketplace. Include:

- repository URL
- current release URL
- screenshots
- security model
- supported MyNode versions / architectures tested
- install and upgrade results
- statement that seeds/private keys are never required
