#!/bin/bash
# Usage: scripts/set-version.sh 0.5.0-beta.2
set -euo pipefail
NEW="${1:?usage: $0 <version>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/mynode-app/xpubwatch"
printf '%s\n' "$NEW" > "$APP/app_data/VERSION"
python3 - "$APP/xpubwatch.json" "$NEW" <<'PY'
import json, sys
path, version = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["latest_version"] = version
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
PY
echo "Version set to $NEW. Add a CHANGELOG entry before tagging v$NEW."
