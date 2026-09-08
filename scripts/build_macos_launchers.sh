#!/bin/zsh
set -euo pipefail
ROOT="${DSPEC_APP_DIR:-$HOME/.dspec/runtime/app}"
DEST="${1:-$HOME/Desktop}"
mkdir -p "$DEST"
for item in "Start DSpec:start" "Stop DSpec:stop" "DSpec Status:status"; do
  name="${item%%:*}"; action="${item##*:}"
  script="do shell script quoted form of \"$ROOT/.venv/bin/python\" & \" \" & quoted form of \"$ROOT/scripts/dspec_runner.py\" & \" $action\""
  if [[ "$action" == "start" ]]; then script="$script
open location \"http://127.0.0.1:3210\""; fi
  /usr/bin/osacompile -o "$DEST/$name.app" -e "$script"
done
print "Created manual launchers in $DEST"
