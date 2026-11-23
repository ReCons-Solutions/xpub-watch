#!/usr/bin/env bash
# Copies the XPUB Watch archive to a MyNode and runs the installer there.
# Runs on your computer (macOS, Linux, WSL, Git Bash).
#
#   ./deploy-to-mynode.sh [archive.tar.gz] [user@node]
#
# Defaults: newest xpubwatch-mynode-community-app-*.tar.gz in the current
# folder or next to this script; node admin@mynode.local (or $MYNODE_HOST).
# Further arguments after the node are passed to the installer (--no-backup).
# With SHA256SUMS next to the archive the checksum is verified first.
# The password is asked once (shared SSH connection).
set -euo pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

ARCHIVE="${1:-}"
TARGET="${2:-${MYNODE_HOST:-admin@mynode.local}}"
shift $(( $# > 2 ? 2 : $# ))
INSTALLER_ARGS=("$@")
REMOTE_DIR="xpubwatch-install"

ARGS=""
for a in ${INSTALLER_ARGS[@]+"${INSTALLER_ARGS[@]}"}; do
  case "$a" in
    *[!A-Za-z0-9=_-]*) die "Unsupported installer argument: $a" ;;
  esac
  ARGS="$ARGS $a"
done

if [ -z "$ARCHIVE" ]; then
  ARCHIVE=$(ls -t ./xpubwatch-mynode-community-app-*.tar.gz \
    "$(dirname "$0")"/xpubwatch-mynode-community-app-*.tar.gz 2>/dev/null | head -n 1 || true)
  [ -n "$ARCHIVE" ] || die "No xpubwatch-mynode-community-app-*.tar.gz found. Usage: $0 [archive.tar.gz] [user@node]"
fi
[ -f "$ARCHIVE" ] || die "Archive not found: $ARCHIVE"
NAME=$(basename "$ARCHIVE")
case "$NAME" in
  *[!A-Za-z0-9._-]*) die "Unexpected characters in file name: $NAME" ;;
esac

SUMS="$(dirname "$ARCHIVE")/SHA256SUMS"
if [ -f "$SUMS" ]; then
  expected=$(awk -v n="$NAME" '$2 == n || $2 == "*" n { print $1 }' "$SUMS")
  if [ -n "$expected" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
      actual=$(sha256sum "$ARCHIVE" | cut -d' ' -f1)
    else
      actual=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
    fi
    [ "$actual" = "$expected" ] || die "Checksum mismatch for $NAME (expected $expected, got $actual)"
    echo "==> Checksum OK"
  else
    echo "==> SHA256SUMS has no entry for $NAME, checksum not verified"
  fi
else
  echo "==> No SHA256SUMS next to the archive, checksum not verified"
fi

SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=/tmp/xpubw-ssh-%C" -o ControlPersist=300)
cleanup() { ssh "${SSH_OPTS[@]}" -O exit "$TARGET" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Connecting to $TARGET"
ssh "${SSH_OPTS[@]}" "$TARGET" "rm -rf ~/$REMOTE_DIR && mkdir -p ~/$REMOTE_DIR"

echo "==> Copying $NAME"
scp "${SSH_OPTS[@]}" "$ARCHIVE" "$TARGET:$REMOTE_DIR/$NAME"

echo "==> Running the installer (sudo asks for the node password)"
set +e
ssh -t "${SSH_OPTS[@]}" "$TARGET" \
  "cd ~/$REMOTE_DIR && tar xzf $NAME && sudo bash xpubwatch/install-on-mynode.sh$ARGS"
status=$?
set -e

if [ "$status" -eq 0 ]; then
  ssh "${SSH_OPTS[@]}" "$TARGET" "rm -rf ~/$REMOTE_DIR" || true
  echo "==> Done"
else
  echo "ERROR: installer failed (exit $status). Files kept on the node in ~/$REMOTE_DIR" >&2
fi
exit "$status"
