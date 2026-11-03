#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/mynode-app/xpubwatch"
VERSION="$(tr -d '[:space:]' < "$APP/app_data/VERSION")"

python3 -m compileall -q "$APP/app_data"
find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} +

python3 - "$APP/xpubwatch.json" "$VERSION" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
version = sys.argv[2]
assert data["short_name"] == "xpubwatch"
assert data["latest_version"] == version, (data["latest_version"], version)
assert data["requires_electrs"] is True
assert data["requires_bitcoin"] is True
print("metadata OK (%s)" % version)
PY

grep -q "^## \[$VERSION\]" "$ROOT/CHANGELOG.md" || { echo "CHANGELOG.md has no entry for $VERSION" >&2; exit 1; }

# Reject common accidental secret/test-data patterns.
if grep -RInE --exclude='validate-release.sh' --exclude-dir=.git --exclude-dir=tests \
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
test -x "$APP/scripts/install_xpubwatch.sh"

# Build twice: the archive must be reproducible and contain the payload.
OUT1="$(mktemp -d)"; OUT2="$(mktemp -d)"
trap 'rm -rf "$OUT1" "$OUT2"' EXIT
python3 "$ROOT/scripts/build-release.py" "$OUT1" >/dev/null
python3 "$ROOT/scripts/build-release.py" "$OUT2" >/dev/null
cmp -s "$OUT1/SHA256SUMS" "$OUT2/SHA256SUMS" || { echo "release archive is not reproducible" >&2; exit 1; }
ARCHIVE="$OUT1/xpubwatch-mynode-community-app-$VERSION.tar.gz"
LISTING="$(tar tzf "$ARCHIVE")"
for required in xpubwatch/xpubwatch.json xpubwatch/app_data/VERSION xpubwatch/app_data/app.py \
                xpubwatch/scripts/install_xpubwatch.sh xpubwatch/xpubwatch.service; do
  grep -qx "$required" <<< "$LISTING" || { echo "missing $required in archive" >&2; exit 1; }
done
if grep -E '__pycache__|\.pyc$|\.db$' <<< "$LISTING"; then
  echo "archive contains build artefacts" >&2; exit 1
fi

echo "XPUB Watch release validation OK ($VERSION)"
