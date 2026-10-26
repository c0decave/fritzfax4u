# Helper tools for release builds: unpack AppImages without running them, inspect ELF files.
FROM docker.io/library/python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends squashfs-tools xz-utils binutils file \
 && rm -rf /var/lib/apt/lists/*
