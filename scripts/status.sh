#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export DSPEC_APP_ROOT="$ROOT"
exec python3 -m dspec.runner status
