# Release build of the fritzfax binary: glibc 2.34 (AlmaLinux 9; PySide6 >= 6.10 and LibreOffice 26.8 need 2.34) so the result runs on
# every current distribution. The Qt runtime libraries are installed so PyInstaller bundles them.
FROM docker.io/library/almalinux:9
RUN dnf -y install epel-release \
 && dnf -y install python3.12 python3.12-pip python3.12-devel gcc binutils findutils xz \
      fontconfig freetype dejavu-sans-fonts libX11 libX11-xcb libxcb xcb-util xcb-util-image xcb-util-keysyms \
      xcb-util-renderutil xcb-util-wm xcb-util-cursor libxkbcommon libxkbcommon-x11 mesa-libGL mesa-libEGL \
      dbus-libs glib2 libXrender libXext libSM libICE libwayland-client libwayland-cursor libwayland-egl \
      cairo nss nspr nss-util cups-libs krb5-libs libXinerama libXrandr libXrender libXcursor libxslt \
 && dnf clean all
RUN python3.12 -m pip install --no-cache-dir PySide6==6.11.1 Pillow==12.1.1 keyring==25.6.0 pyinstaller==6.11.1
ENV QT_QPA_PLATFORM=offscreen
WORKDIR /build
