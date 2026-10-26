#!/bin/sh
# Remove fritzfax packages of versions older than VERSION from DIR; newer versions and anything
# that is not a fritzfax-<version>-* file stay.   Usage: tools/prune_dist.sh VERSION DIR
set -eu
[ $# -eq 2 ] || { echo "usage: $0 VERSION DIR" >&2; exit 2; }
version=$1
dir=$2
for f in "$dir"/fritzfax-*; do
    [ -f "$f" ] || continue
    v=${f##*/fritzfax-}
    v=${v%%-*}
    [ "$v" = "$version" ] && continue
    newest=$(printf '%s\n%s\n' "$v" "$version" | sort -V | tail -n 1)
    if [ "$newest" = "$version" ]; then
        rm -f "$f"
    fi
done
