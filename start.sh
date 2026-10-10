#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
exec python3 sc_hub.py "$@"
