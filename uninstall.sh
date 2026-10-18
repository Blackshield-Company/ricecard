#!/usr/bin/env bash
set -euo pipefail
rm -f "$HOME/.local/bin/ricecard"; rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/ricecard"; echo "ricecard removed"
