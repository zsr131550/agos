#!/bin/sh
# Managed by AGOS. Human-developer hook only.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
agos ci --local --stage __STAGE__ "$@"

LEGACY_HOOK="$SCRIPT_DIR/__LEGACY_HOOK__"
if [ -x "$LEGACY_HOOK" ]; then
  exec "$LEGACY_HOOK" "$@"
fi
if [ -f "$LEGACY_HOOK" ]; then
  exec /bin/sh "$LEGACY_HOOK" "$@"
fi
