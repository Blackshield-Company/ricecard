#!/usr/bin/env bash
# Ricecard installer: user-local, no sudo, no pip/network needed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/ricecard"; bin="$HOME/.local/bin"
python3 -c 'import PIL' 2>/dev/null || { echo "ricecard needs Pillow: sudo pacman -S python-pillow" >&2; exit 1; }
mkdir -p "$dest" "$bin"
install -m644 "$here/ricecard.py" "$dest/ricecard.py"
printf '#!/bin/sh\nexec python3 "%s/ricecard.py" "$@"\n' "$dest" > "$bin/ricecard"; chmod 755 "$bin/ricecard"
echo "installed: $bin/ricecard"
case ":$PATH:" in *":$bin:"*) ;; *) echo "note: add $bin to your PATH";; esac
