#!/bin/bash
set -euo pipefail
: "${INSTALL_FOLDER:?INSTALL_FOLDER not set}"
: "${STORAGE_FOLDER:?STORAGE_FOLDER not set}"
VERSION="0.4.0-beta.1"

mkdir -p "$STORAGE_FOLDER"
cp -a "$INSTALL_FOLDER/app_data/." "$INSTALL_FOLDER/"
rm -rf "$INSTALL_FOLDER/app_data"

python3 -m venv "$INSTALL_FOLDER/.venv"
"$INSTALL_FOLDER/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$INSTALL_FOLDER/requirements.txt"
"$INSTALL_FOLDER/.venv/bin/python" -c 'import flask, embit, gunicorn; print("XPUB Watch Python dependencies OK")'

if [ ! -f "$STORAGE_FOLDER/flask_secret" ]; then
  python3 - <<'PY' > "$STORAGE_FOLDER/flask_secret"
import secrets
print(secrets.token_hex(32))
PY
  chmod 600 "$STORAGE_FOLDER/flask_secret"
fi

mkdir -p /home/bitcoin/.mynode /mnt/hdd/mynode/settings
printf '%s\n' "$VERSION" > /home/bitcoin/.mynode/xpubwatch_version
printf '%s\n' "$VERSION" > /mnt/hdd/mynode/settings/xpubwatch_version
chown bitcoin:bitcoin /home/bitcoin/.mynode/xpubwatch_version /mnt/hdd/mynode/settings/xpubwatch_version "$STORAGE_FOLDER/flask_secret"
