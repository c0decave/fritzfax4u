#!/bin/sh
# Remove fritzfax installed by install.sh. Settings (~/.config/fritzfax) and the send log
# (~/.local/share/fritzfax) are kept; delete them yourself if you want.
# Usage: ./uninstall.sh [--prefix DIR]   (default prefix: ~/.local)
set -eu
usage() { echo "usage: $0 [--prefix DIR]" >&2; exit 2; }
prefix="$HOME/.local"
case "${1:-}" in
    "") ;;
    --prefix) [ $# -eq 2 ] && [ -n "$2" ] || usage; prefix="$2" ;;
    *) usage ;;
esac
[ -d "$prefix" ] && prefix=$(cd "$prefix" && pwd)
dest="$prefix/opt/fritzfax"
# only the commands install.sh created: symlinks into our program directory
for cmd in fritzfax fritzfax-gui; do
    link="$prefix/bin/$cmd"
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$dest/fritzfax/fritzfax" ]; then
        rm -f "$link"
    elif [ -e "$link" ]; then
        echo "note: $link was not installed by fritzfax's install.sh, left in place"
    fi
done
rm -f "$prefix/share/applications/fritzfax.desktop"
rm -rf "$dest"
echo "fritzfax removed (settings and send log kept)"
