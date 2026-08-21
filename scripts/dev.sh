#!/usr/bin/env bash
# Foreground helper. Prefer ./scripts/app.sh for start/stop/status/debug.
exec "$(cd "$(dirname "$0")" && pwd)/app.sh" start --foreground "$@"
