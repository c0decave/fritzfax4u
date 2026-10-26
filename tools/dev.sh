#!/usr/bin/env bash
# Run a command inside the fritzfax dev container.
#   tools/dev.sh build            build the image (needs network)
#   tools/dev.sh pytest [args]    run tests offline (--network=none)
#   tools/dev.sh live [args]      run live tests (network allowed; needs FRITZFAX_* env)
set -euo pipefail
IMAGE=localhost/fritzfax-dev:1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMMON=(--rm --cap-drop=ALL --security-opt=no-new-privileges --pids-limit 512 --memory 4g
        --tmpfs /tmp:rw,size=1g -v "$ROOT":/src:Z -w /src
        -v /etc/localtime:/etc/localtime:ro)
case "${1:-}" in
  build) podman build -t "$IMAGE" -f "$ROOT/Containerfile" "$ROOT" ;;
  pytest) shift; podman run "${COMMON[@]}" --network=none "$IMAGE" python -m pytest -p no:cacheprovider "$@" ;;
  live) shift; podman run "${COMMON[@]}" -e FRITZFAX_LIVE=1 -e FRITZFAX_URL -e FRITZFAX_USER -e FRITZFAX_PASSWORD \
          -e FRITZFAX_LIVE_SEND "$IMAGE" python -m pytest -p no:cacheprovider tests/live "$@" ;;
  run) shift; podman run -i "${COMMON[@]}" --network=none "$IMAGE" "$@" ;;
  *) echo "usage: $0 build|pytest|live|run" >&2; exit 2 ;;
esac
