#!/bin/bash
set -euo pipefail
: "${INSTALL_FOLDER:?INSTALL_FOLDER not set}"
: "${STORAGE_FOLDER:?STORAGE_FOLDER not set}"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "XPUB Watch requires Python 3.8 or newer (found $(python3 --version 2>&1))" >&2
  exit 1
fi

mkdir -p "$STORAGE_FOLDER"
chmod 700 "$STORAGE_FOLDER"
cp -a "$INSTALL_FOLDER/app_data/." "$INSTALL_FOLDER/"
rm -rf "$INSTALL_FOLDER/app_data"
VERSION="$(tr -d '[:space:]' < "$INSTALL_FOLDER/VERSION")"

python3 -m venv "$INSTALL_FOLDER/.venv"
"$INSTALL_FOLDER/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$INSTALL_FOLDER/requirements.txt"
"$INSTALL_FOLDER/.venv/bin/python" -c 'import flask, embit, gunicorn; print("XPUB Watch Python dependencies OK")'

if [ ! -s "$STORAGE_FOLDER/flask_secret" ]; then
  python3 -c 'import secrets; print(secrets.token_hex(32))' > "$STORAGE_FOLDER/flask_secret"
fi
chmod 600 "$STORAGE_FOLDER/flask_secret"

mkdir -p /home/bitcoin/.mynode /mnt/hdd/mynode/settings
printf '%s\n' "$VERSION" > /home/bitcoin/.mynode/xpubwatch_version
printf '%s\n' "$VERSION" > /mnt/hdd/mynode/settings/xpubwatch_version
chown bitcoin:bitcoin /home/bitcoin/.mynode/xpubwatch_version /mnt/hdd/mynode/settings/xpubwatch_version
chown -R bitcoin:bitcoin "$STORAGE_FOLDER"
