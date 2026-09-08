#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT/frontend"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
npm run typecheck
npm run build
test -f "$ROOT/frontend/out/index.html"
echo "Frontend static export built at $ROOT/frontend/out"
