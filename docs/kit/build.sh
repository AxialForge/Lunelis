#!/bin/bash
# usage: docs/kit/build.sh <source.md> [out-dir]     (default out-dir: docs/manuals)
set -e
KIT="$(cd "$(dirname "$0")" && pwd)"
SRC="$1"; OUT="${2:-$KIT/../manuals}"
[ -f "$SRC" ] || { echo "usage: build.sh <source.md> [out-dir]"; exit 2; }
export NODE_PATH="${NODE_PATH:-$(npm root -g 2>/dev/null)}:$KIT/node_modules"
[ -d "$KIT/node_modules/docx" ] || export NODE_PATH="$NODE_PATH:/opt/node-tools/node_modules"
node "$KIT/render.js" "$SRC" "$OUT"
NAME="$(basename "${SRC%.md}")"
python3 "$KIT/finish.py" "$OUT/$NAME.docx"
python3 "$KIT/check.py" "$OUT/$NAME.pdf" "$SRC" || true
