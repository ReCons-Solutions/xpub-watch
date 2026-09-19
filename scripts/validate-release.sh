#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/mynode-app/xpubwatch"
VERSION="0.4.0-beta.1"

python3 -m compileall -q "$APP/app_data"

python3 - <<PY
import json
from pathlib import Path
p=Path("$APP/xpubwatch.json")
data=json.loads(p.read_text())
assert data["short_name"] == "xpubwatch"
assert data["latest_version"] == "$VERSION", data["latest_version"]
assert data["requires_electrs"] is True
assert data["requires_bitcoin"] is True
print("metadata OK")
PY

# Reject common accidental secret/test-data patterns.
if grep -RInE --exclude='validate-release.sh' \
  '192\.168\.[0-9]+\.[0-9]+|zpub6r4zncxgKNQ|460542474051761d|d00a92a0fe45bc|api\.telegram\.org/bot[0-9]+:' \
  "$ROOT"; then
  echo "Potential private/test data found" >&2
  exit 1
fi

for f in README.md LICENSE SECURITY.md CHANGELOG.md CONTRIBUTING.md; do
  test -s "$ROOT/$f"
done

test -s "$APP/xpubwatch.png"
test -s "$APP/app_data/static/xpubwatch-logo.png"

echo "XPUB Watch release validation OK"
