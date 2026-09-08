#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must run on macOS." >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_ROOT="$HOME/.dspec"
INSTALL="$HOME_ROOT/source"
VENV="$HOME_ROOT/venv"
BIN="$HOME_ROOT/bin"
APPS="$HOME/Applications"

mkdir -p "$HOME_ROOT" "$BIN" "$APPS"

if [[ ! -f "$ROOT/frontend/out/index.html" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "frontend/out is missing and Node/npm is unavailable. Build the release package first." >&2
    exit 3
  fi
  "$ROOT/scripts/build-frontend.sh"
fi

rm -rf "$INSTALL"
mkdir -p "$INSTALL"
cp -R "$ROOT/dspec" "$ROOT/frontend" "$ROOT/scripts" "$ROOT/pyproject.toml" "$ROOT/requirements.txt" "$ROOT/README.md" "$INSTALL/"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/pip" install "$INSTALL[tray]"

cat > "$BIN/dspec" <<'SH'
#!/bin/sh
export DSPEC_APP_ROOT="$HOME/.dspec/source"
exec "$HOME/.dspec/venv/bin/python" -m dspec.runner "$@"
SH
chmod 700 "$BIN/dspec"

make_app() {
  local name="$1"
  local body="$2"
  local target="$APPS/$name.app"
  rm -rf "$target"
  osacompile -o "$target" -e "$body"
}

make_app "Start DSpec" "do shell script quoted form of POSIX path of \"$BIN/dspec\" & \" start\""
make_app "Stop DSpec" "do shell script quoted form of POSIX path of \"$BIN/dspec\" & \" stop\""
make_app "DSpec Status" "set resultText to do shell script quoted form of POSIX path of \"$BIN/dspec\" & \" status\""$'\n'"display dialog resultText with title \"DSpec Status\" buttons {\"OK\"} default button \"OK\""

echo "Installed DSpec under $INSTALL"
echo "Launchers created in $APPS:"
echo "  Start DSpec.app"
echo "  Stop DSpec.app"
echo "  DSpec Status.app"
echo "No Login Item, LaunchAgent, or boot daemon was created."
