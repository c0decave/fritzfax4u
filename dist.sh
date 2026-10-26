#!/bin/sh
# Build the release packages of the committed version:
#   dist/fritzfax-<v>-linux-x86_64.tar.xz              program only (PDF and images; office documents
#                                                      use a system LibreOffice if there is one)
#   dist/fritzfax-<v>-linux-x86_64-libreoffice.tar.xz  program + LibreOffice (office documents work
#                                                      without installing anything)
#   dist/fritzfax-<v>-source.tar.gz                    the committed source
#   *.sha256                                           checksums
# Runs on glibc >= 2.34 (built on AlmaLinux 9). Everything is built from "git archive HEAD" inside
# podman, offline; only a missing LibreOffice AppImage is downloaded (pinned in tools/libreoffice.lock).
# Before anything is built the source is scanned for private data (tools/privacy_scan.py).
set -eu
cd "$(dirname "$0")"
root=$(pwd)
# build cache outside the repository (LibreOffice is large; tools that walk the tree must not see it)
cache="${FRITZFAX_DIST_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/fritzfax-dist}"

if [ -n "$(git status --porcelain)" ]; then
    echo "Uncommitted changes: commit first, the package must match a commit." >&2
    exit 1
fi

version=$(cat VERSION)
commit=$(git rev-parse --short HEAD)
name="fritzfax-${version}-linux-x86_64"
builder=localhost/fritzfax-builder:1
tools_image=localhost/fritzfax-disttools:1
run="podman run --rm --cap-drop=ALL --security-opt=no-new-privileges"

mkdir -p "$cache/lo" dist
work=$(mktemp -d "$cache/work-XXXXXX")
trap 'rm -rf "$work"' EXIT

echo "== source of $commit"
mkdir -p "$work/src"
git archive -o "$work/src.tar" HEAD   # no pipe: /bin/sh would not see a failing git archive
tar -xf "$work/src.tar" -C "$work/src"
if ! python3 tools/privacy_scan.py "$work/src"; then
    echo "Private data in the source, nothing was built." >&2
    exit 1
fi

echo "== images"
podman image exists "$builder" || podman build -q -t "$builder" -f tools/containers/builder.Containerfile tools/containers
podman image exists "$tools_image" || podman build -q -t "$tools_image" -f tools/containers/disttools.Containerfile tools/containers

echo "== LibreOffice"
lo_name=$(sed -n 's/^name=//p' tools/libreoffice.lock)
lo_url=$(sed -n 's/^url=//p' tools/libreoffice.lock)
lo_sha=$(sed -n 's/^sha256=//p' tools/libreoffice.lock)
if [ ! -f "$cache/lo/$lo_name" ]; then
    $run -v "$cache/lo":/out:Z "$builder" python3.12 -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], '/out/' + sys.argv[2] + '.part')" "$lo_url" "$lo_name"
    mv "$cache/lo/$lo_name.part" "$cache/lo/$lo_name"
fi
echo "$lo_sha  $cache/lo/$lo_name" | sha256sum -c --quiet || { echo "LibreOffice checksum mismatch" >&2; exit 1; }
offset=$(python3 -c "
import struct, sys
h = open(sys.argv[1], 'rb').read(64)
shoff, = struct.unpack_from('<Q', h, 0x28); size, num = struct.unpack_from('<HH', h, 0x3A)
print(shoff + size * num)" "$cache/lo/$lo_name")
# unpack without running the AppImage
$run --network=none -v "$cache/lo":/lo:ro,Z -v "$work":/w:Z "$tools_image" \
    sh -c "unsquashfs -q -no-xattrs -o $offset -d /w/lo-root /lo/$lo_name >/dev/null"
lo_dir=$(ls -d "$work"/lo-root/opt/libreoffice* | head -1)
lo_rel=$(basename "$lo_dir")
lo_version=$(basename "$lo_name" | sed 's/LibreOffice-\([0-9.]*\)\..*/\1/')

echo "== program"
$run --network=none -v "$work":/w:Z -w /w/src "$builder" sh -c '
    python3.12 -m PyInstaller --noconfirm --clean --log-level WARN --distpath /w/out --workpath /w/build tools/fritzfax.spec >/w/pyinstaller.log 2>&1 || { tail -30 /w/pyinstaller.log; exit 1; }
    mkdir -p /w/licenses
    for d in /usr/local/lib64/python3.12/site-packages/*.dist-info /usr/local/lib/python3.12/site-packages/*.dist-info; do
        for f in "$d"/LICENSE* "$d"/licenses/* "$d"/COPYING*; do
            [ -f "$f" ] && cp "$f" "/w/licenses/$(basename "$d" .dist-info)-$(basename "$f")"
        done
    done
    cp /usr/share/licenses/python3.12/LICENSE /w/licenses/python-3.12-LICENSE 2>/dev/null || true
    python3.12 /w/src/tools/collect_libs.py --qt-fallback /w/out/fritzfax/_internal /w/out/fritzfax/fallback-libs
    mkdir -p /w/lo-runtime && python3.12 /w/src/tools/collect_libs.py "/w/lo-root/opt/$0" /w/lo-runtime' "$lo_rel"

echo "== assemble"
slim="$work/pkg/$name"
mkdir -p "$slim/LICENSES"
cp -a "$work/out/fritzfax" "$slim/fritzfax"
cp tools/package/install.sh tools/package/uninstall.sh tools/package/fritzfax.desktop "$slim/"
cp tools/package/fonts.conf "$slim/fritzfax/fallback-libs/"  # for systems without /etc/fonts (fritzfax/syslibs.py)
sed -e "s/__VERSION__/$version/" -e "s/__LIBREOFFICE__/$lo_version/" tools/package/README.txt > "$slim/README.txt"
chmod 755 "$slim/install.sh" "$slim/uninstall.sh"
echo "$version" > "$slim/VERSION"
echo "$commit" > "$slim/COMMIT"
cp LICENSE "$slim/LICENSES/fritzfax-LICENSE"
cp "$work"/licenses/* "$slim/LICENSES/"
cp tools/package/licenses/* "$slim/LICENSES/"  # Qt for Python: its wheels carry no license files
full="$work/pkg/$name-libreoffice"
cp -a "$slim" "$full"
cp -a "$lo_dir" "$full/libreoffice"
mkdir -p "$full/libreoffice/fritzfax-runtime"
cp -a "$work/lo-runtime/." "$full/libreoffice/fritzfax-runtime/"
cp tools/package/fonts.conf "$full/libreoffice/fritzfax-runtime/"
cp "$full/libreoffice/LICENSE" "$full/LICENSES/libreoffice-LICENSE" 2>/dev/null || true

echo "== selftest of the packages (offline, inside the build image)"
$run --network=none -v "$work/pkg":/pkg:ro,Z "$builder" sh -c "
    set -e
    /pkg/$name/fritzfax/fritzfax selftest
    /pkg/$name-libreoffice/fritzfax/fritzfax selftest > /tmp/full.txt || { cat /tmp/full.txt; exit 1; }
    cat /tmp/full.txt
    grep -q '^OK    LibreOffice' /tmp/full.txt" || { echo "selftest failed, nothing was published" >&2; exit 1; }

echo "== archives"
for p in "$name" "$name-libreoffice"; do
    # no pipe: /bin/sh would only see the exit status of xz, not of tar
    XZ_OPT="-T0 -6" tar -C "$work/pkg" -cJf "dist/$p.tar.xz.part" "$p"
    mv "dist/$p.tar.xz.part" "dist/$p.tar.xz"
    (cd dist && sha256sum "$p.tar.xz" > "$p.tar.xz.sha256")
done
git archive --format=tar.gz --prefix="fritzfax-${version}-source/" -o "dist/fritzfax-${version}-source.tar.gz" HEAD
(cd dist && sha256sum "fritzfax-${version}-source.tar.gz" > "fritzfax-${version}-source.tar.gz.sha256")

# remove packages of older versions; newer ones (built from a later commit) stay
sh tools/prune_dist.sh "$version" dist
ls -la dist/
