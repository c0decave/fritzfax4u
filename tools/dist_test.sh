#!/bin/sh
# Test the release packages in dist/ on several distributions (minimal images, offline):
# unpack, selftest, install.sh, selftest of the installed copy; the full package must convert
# an office document with its bundled LibreOffice. With --gui additionally an X11 run under Xvfb.
# Any "Fontconfig" line fails a run: bundled libraries must never hide newer system ones.
#   tools/dist_test.sh [--gui] [image ...]
set -eu
cd "$(dirname "$0")/.."
gui=0
[ "${1:-}" = "--gui" ] && { gui=1; shift; }
images=${*:-"docker.io/library/debian:12 docker.io/library/debian:13 docker.io/library/ubuntu:22.04 docker.io/library/ubuntu:24.04 docker.io/library/fedora:42 docker.io/library/archlinux:latest docker.io/gentoo/stage3:latest"}
version=$(cat VERSION)
name="fritzfax-${version}-linux-x86_64"
cache="${FRITZFAX_DIST_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/fritzfax-dist}"
work=$(mktemp -d "$cache/test-XXXXXX")
trap 'rm -rf "$work"' EXIT
for p in "$name" "$name-libreoffice"; do
    (cd dist && sha256sum -c --quiet "$p.tar.xz.sha256")
    tar -C "$work" -xJf "dist/$p.tar.xz"
done
failed=""
for image in $images; do
    podman image exists "$image" || podman pull -q "$image" >/dev/null
    log="$work/$(echo "$image" | tr '/:' '__').log"
    if podman run --rm --network=none --cap-drop=ALL --security-opt=no-new-privileges -v "$work":/pkg:ro,Z "$image" sh -c "
        set -e
        export HOME=/tmp/home LANG=C.UTF-8
        mkdir -p \$HOME
        /pkg/$name/fritzfax/fritzfax --version
        /pkg/$name/fritzfax/fritzfax selftest
        /pkg/$name-libreoffice/fritzfax/fritzfax selftest > /tmp/full.txt || { cat /tmp/full.txt; exit 1; }
        cat /tmp/full.txt
        grep -q '^OK    LibreOffice' /tmp/full.txt
        grep -q '^OK    system libraries' /tmp/full.txt
        cp -a /pkg/$name-libreoffice /tmp/pkg && /tmp/pkg/install.sh
        \$HOME/.local/bin/fritzfax selftest > /tmp/inst.txt || { cat /tmp/inst.txt; exit 1; }
        grep -q '^OK    LibreOffice' /tmp/inst.txt
        \$HOME/.local/bin/fritzfax-gui --help >/dev/null 2>&1 || true
        /tmp/pkg/uninstall.sh
        test ! -e \$HOME/.local/bin/fritzfax
    " > "$log" 2>&1 && ! grep -q 'Fontconfig' "$log"; then
        echo "PASS  $image  ($(grep -m1 'glibc' "$log"))"
    else
        echo "FAIL  $image"; tail -15 "$log" | sed 's/^/      /'
        failed="$failed $image"
    fi
done
if [ "$gui" = 1 ]; then
    for base in docker.io/library/debian:12 docker.io/library/archlinux:latest; do
        tag="localhost/fritzfax-xvfb-$(echo "$base" | sed 's#.*/##; s/:/-/')"
        if ! podman image exists "$tag"; then
            case "$base" in
                *debian*) inst="apt-get update -qq && apt-get install -y -qq xvfb xauth >/dev/null" ;;
                *arch*) inst="pacman -Sy --noconfirm --quiet xorg-server-xvfb >/dev/null" ;;
            esac
            printf 'FROM %s\nRUN %s\n' "$base" "$inst" | podman build -q -t "$tag" -f - . >/dev/null
        fi
        if podman run --rm --network=none --cap-drop=ALL --security-opt=no-new-privileges -v "$work":/pkg:ro,Z "$tag" sh -c "
            export HOME=/tmp/home LANG=C.UTF-8 QT_QPA_PLATFORM=xcb; mkdir -p \$HOME
            Xvfb :99 -screen 0 1280x1024x24 >/dev/null 2>&1 & sleep 2; export DISPLAY=:99
            /pkg/$name-libreoffice/fritzfax/fritzfax selftest --gui" > "$work/gui.log" 2>&1 &&
           grep -q 'platform xcb' "$work/gui.log" && grep -q '^OK    GUI' "$work/gui.log" &&
           grep -q '^OK    system libraries' "$work/gui.log" && ! grep -q 'Fontconfig' "$work/gui.log"; then
            echo "PASS  $base  X11 GUI (xcb under Xvfb)"
        else
            echo "FAIL  $base  X11 GUI"; tail -15 "$work/gui.log" | sed 's/^/      /'
            failed="$failed $base-gui"
        fi
    done
fi
[ -z "$failed" ] && echo "all distribution tests passed" || { echo "failed:$failed"; exit 1; }
