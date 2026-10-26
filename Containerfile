# Dev/test image for fritzfax. Build once with network; run tests with --network=none.
FROM docker.io/library/python:3.13-slim-trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui \
      fonts-dejavu-core fontconfig \
      libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libglib2.0-0t64 \
      openssl \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
      PySide6==6.11.1 Pillow==12.1.1 pytest==8.3.5 pytest-qt==4.4.0 keyring==25.6.0 setuptools==75.8.0
ENV QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
