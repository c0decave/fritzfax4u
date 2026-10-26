#!/bin/sh
# Install fritzfax for the current user (no root needed):
#   program      ~/.local/opt/fritzfax/        (this package)
#   commands     ~/.local/bin/fritzfax, ~/.local/bin/fritzfax-gui
#   menu entry   ~/.local/share/applications/fritzfax.desktop
# Usage: ./install.sh [--prefix DIR]   (default prefix: ~/.local)
set -eu
usage() { echo "usage: $0 [--prefix DIR]" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
prefix="$HOME/.local"
case "${1:-}" in
    "") ;;
    --prefix) [ $# -eq 2 ] && [ -n "$2" ] || usage; prefix="$2" ;;
    *) usage ;;
esac
mkdir -p "$prefix"
prefix=$(cd "$prefix" && pwd)  # absolute: the command symlinks must not depend on the current directory
dest="$prefix/opt/fritzfax"
mkdir -p "$prefix/opt" "$prefix/bin" "$prefix/share/applications"

# never replace a fritzfax command that is not ours (for example one from "pip install --user")
for cmd in fritzfax fritzfax-gui; do
    link="$prefix/bin/$cmd"
    if [ -e "$link" ] || [ -L "$link" ]; then
        if [ ! -L "$link" ] || [ "$(readlink "$link")" != "$dest/fritzfax/fritzfax" ]; then
            echo "error: $link exists and was not installed by this script; remove it first" >&2
            exit 1
        fi
    fi
done

if [ "$here" != "$dest" ]; then
    rm -rf "$dest.new"
    cp -a "$here" "$dest.new"
    rm -rf "$dest"
    mv "$dest.new" "$dest"
fi
ln -sf "$dest/fritzfax/fritzfax" "$prefix/bin/fritzfax"
ln -sf "$dest/fritzfax/fritzfax" "$prefix/bin/fritzfax-gui"

# menu entry: Exec takes a quoted argument (Desktop Entry spec), in which \ " ` $ are escaped
# with a backslash; as Exec is a string value, each backslash is then escaped once more; a
# literal % is written %% (field codes)
quoted=$(printf '%s' "$prefix/bin/fritzfax-gui" | sed -e 's/[\\"`$]/\\&/g' -e 's/\\/\\\\/g' -e 's/%/%%/g')
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
        Exec=*) printf 'Exec="%s" %%F\n' "$quoted" ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$dest/fritzfax.desktop" > "$prefix/share/applications/fritzfax.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$prefix/share/applications" || true

echo "fritzfax $(cat "$dest/VERSION") installed to $dest"
[ -d "$dest/libreoffice" ] && echo "LibreOffice for office documents is included."
case ":$PATH:" in *":$prefix/bin:"*) ;; *) echo "note: add $prefix/bin to your PATH";; esac
if "$prefix/bin/fritzfax" selftest >/dev/null 2>&1; then
    echo "selftest passed"
else
    echo "selftest reported a problem: run '$prefix/bin/fritzfax selftest'"
fi
