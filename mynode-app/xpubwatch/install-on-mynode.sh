#!/bin/bash
# Installs or upgrades XPUB Watch on a MyNode device from an extracted release archive.
#
#   tar xzf xpubwatch-mynode-community-app-<version>.tar.gz
#   sudo bash xpubwatch/install-on-mynode.sh
#
# Steps: backup of the data folder (if any) -> copy the app folder to
# /usr/share/mynode_apps/xpubwatch -> mynode-manage-apps init + install
# (reinstall for the same version) -> enable and start the service.
set -euo pipefail

APP=xpubwatch
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Paths can be overridden for testing only.
APPS_DIR="${XPUBW_APPS_DIR:-/usr/share/mynode_apps}"
DATA_DIR="${XPUBW_DATA_DIR:-/mnt/hdd/mynode/xpubwatch}"
VERSION_FILE="${XPUBW_VERSION_FILE:-/home/bitcoin/.mynode/${APP}_version}"
MANAGE="${XPUBW_MANAGE:-mynode-manage-apps}"
SYSTEMCTL="${XPUBW_SYSTEMCTL:-systemctl}"
WAIT="${XPUBW_WAIT:-30}"
NO_BACKUP=0

for arg in "$@"; do
  case "$arg" in
    --no-backup) NO_BACKUP=1 ;;
    -h|--help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      echo "Options: --no-backup  skip the backup of $DATA_DIR"
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || [ -n "${XPUBW_APPS_DIR:-}" ] || die "Run with sudo: sudo bash $0"
[ -f "$SRC/$APP.json" ] && [ -f "$SRC/app_data/VERSION" ] || die "Run this script from the extracted '$APP' folder of the release archive."
command -v "$MANAGE" >/dev/null 2>&1 || die "'mynode-manage-apps' not found. Is this a MyNode device with a current MyNode version?"
[ -d "$APPS_DIR" ] || die "$APPS_DIR does not exist. This MyNode version does not support dynamic apps."
[ -d "$(dirname "$DATA_DIR")" ] || die "The MyNode data drive is not mounted ($(dirname "$DATA_DIR") missing)."

VERSION="$(tr -d '[:space:]' < "$SRC/app_data/VERSION")"
INSTALLED="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null || true)"
echo "XPUB Watch $VERSION (installed: ${INSTALLED:-none})"

if [ -d "$DATA_DIR" ] && [ "$NO_BACKUP" -eq 0 ]; then
  BACKUP="${DATA_DIR}-backup-$(date +%Y%m%d-%H%M%S)"
  step "Backing up $DATA_DIR to $BACKUP"
  "$SYSTEMCTL" stop "$APP" 2>/dev/null || true
  cp -a "$DATA_DIR" "$BACKUP"
  chmod 700 "$BACKUP"
fi

step "Copying app files to $APPS_DIR/$APP"
[ "$SRC" != "$APPS_DIR/$APP" ] || die "Run the script from the extracted archive, not from $APPS_DIR."
rm -rf "$APPS_DIR/$APP"
mkdir -p "$APPS_DIR/$APP"
cp -a "$SRC/." "$APPS_DIR/$APP/"
rm -f "$APPS_DIR/$APP/install-on-mynode.sh"
touch "$APPS_DIR/$APP/is_manually_added"

step "Registering the app (mynode-manage-apps init)"
"$MANAGE" init

if [ "$INSTALLED" = "$VERSION" ]; then
  step "Reinstalling $VERSION (this can take a few minutes)"
  "$MANAGE" reinstall "$APP"
else
  step "Installing $VERSION (this can take a few minutes)"
  "$MANAGE" install "$APP"
fi

NOW="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null || true)"
[ "$NOW" = "$VERSION" ] || die "Installation failed (version file: '${NOW:-missing}'). See the mynode-manage-apps output above."

step "Enabling and starting the service"
"$SYSTEMCTL" enable "$APP" >/dev/null 2>&1 || true
"$SYSTEMCTL" restart "$APP"
for _ in $(seq 1 "$WAIT"); do
  "$SYSTEMCTL" is-active --quiet "$APP" && break
  sleep 1
done
"$SYSTEMCTL" is-active --quiet "$APP" || die "The service did not start. Details: sudo journalctl -u $APP -n 100 --no-pager"

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

XPUB Watch $VERSION is installed and running.

  Open:  https://${IP:-<node-ip>}:5116  (or the XPUB Watch tile on the MyNode dashboard)
  Then set the access password immediately.

  Status: systemctl status $APP --no-pager
  Logs:   sudo journalctl -u $APP -n 100 --no-pager
EOF
