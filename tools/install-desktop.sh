#!/usr/bin/env bash
# Install the menu entry for fritzfax-gui for the current user.
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)/fritzfax/data/fritzfax.desktop"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$dest"
install -m 0644 "$src" "$dest/fritzfax.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$dest" || true
echo "installed $dest/fritzfax.desktop"
