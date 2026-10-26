# fritzfax4u

**fritzfax** sendet Faxe über eine AVM FRITZ!Box: ein Programm für Linux, Windows und macOS
(Deutsch und Englisch) und eine Kommandozeile.

[English](README.md)

![fritzfax, helles Theme](docs/images/fritzfax-light.png)

fritzfax nutzt dasselbe HTTP-Protokoll wie die Weboberfläche der Box. Es braucht **kein CAPI über
TCP** (deshalb funktionieren Roger Router & Co. nicht mehr) und kein SIP-Konto. Details in
[docs/protocol.md](docs/protocol.md).

## Funktionen

* **Das Dokument ist das Fax.** Eine einseitige PDF wird als einseitiges Fax gesendet, ohne
  erzwungenes Anschreiben. Brieftext, Deckblatt und Kopfzeile sind optional.
* PDF, Bilder (auch mehrseitige TIFF) und Office-Dokumente (mit LibreOffice).
* Eine Vorschau genau der Seiten, die gesendet werden. Einzelne Seiten entfernen Sie mit Entf oder
  per Rechtsklick.
* Mehrere Empfänger nacheinander; Nummern dürfen als `0531 49059113`, `0531-49059113` oder
  `+49 (0)531 49059113` geschrieben werden. Die eigenen Nummern der Box erscheinen als `+49 30 …`.
* Bestätigung per zweitem Faktor (Taste an der Box, `*1<Code>`, Authenticator).
* **Jedes Fax wird mit den eigenen Aufzeichnungen der Box geprüft**: Endstatus, Eintrag im
  Fax-Journal, die Kopie der Seiten in der Box (bitgenau verglichen) und die Anrufliste. Ein
  lokales Sendeprotokoll hält fest, wann, an wen, mit welchem Inhalt und mit welchem Ergebnis
  gesendet wurde, mit PDF-Kopie und Sendebericht.
* Dauert ein Gespräch länger als erwartet, wartet fritzfax auf das Ergebnis der Box, statt zu raten.
* TLS-Zertifikat wird gepinnt; das Passwort liegt im System-Keyring (nur wenn Sie das Häkchen
  setzen), nie in der Einstellungsdatei. Keine eingebaute Box-Adresse, keine Telemetrie.
* Helles, dunkles oder System-Theme; ein (?) neben jeder Einstellung erklärt sie. Verbindet sich beim Start.
* Optional (standardmäßig aus): eine Animation mit Faxgeräusch und Fanfare beim Senden.

![fritzfax, dunkles Theme mit Brieftext und Deckblatt](docs/images/fritzfax-dark.png)

Getestet mit einer FRITZ!Box 7590 und FRITZ!OS 8.25: echte Faxe gesendet und empfangen.

## Download

Fertige Pakete gibt es auf der [Release-Seite](https://github.com/c0decave/fritzfax4u/releases):

| System | Paket | Start |
|---|---|---|
| Linux x86_64 (glibc ≥ 2.34; getestet auf Debian 12/13, Ubuntu 22.04/24.04, Fedora 42, Arch, Gentoo) | `fritzfax-<version>-linux-x86_64-libreoffice.tar.xz` (mit LibreOffice) oder `…-linux-x86_64.tar.xz` (PDF und Bilder) | `./install.sh`, dann `fritzfax-gui` |
| Windows x86_64 (gebaut und getestet auf Windows Server 2022) | `fritzfax-<version>-windows-x86_64.zip` | entpacken, `fritzfax-gui.exe` |
| macOS, Apple Silicon (gebaut und getestet auf macOS 14) | `fritzfax-<version>-macos-arm64.zip` | `fritzfax.app` nach Programme ziehen |

Die Pakete für Windows und macOS sind nicht signiert: Windows fragt „Trotzdem ausführen?“, macOS
will beim ersten Start Rechtsklick → Öffnen (Details in der README.txt im Paket). Office-Dokumente
brauchen unter Windows und macOS ein installiertes LibreOffice; PDF und Bilder gehen immer.

Zu jedem Paket gibt es eine `.sha256`-Datei. Ein Paket prüfen Sie mit `fritzfax selftest` (oder `--gui`).

### Linux

```sh
tar xf fritzfax-<version>-linux-x86_64-libreoffice.tar.xz
cd fritzfax-<version>-linux-x86_64-libreoffice && ./install.sh   # nach ~/.local, Menüeintrag, Selbsttest
./uninstall.sh                                                    # wieder entfernen (Einstellungen und Protokoll bleiben)
```

`install.sh --prefix DIR` installiert woanders hin. Einen `fritzfax`-Befehl, den es nicht selbst
installiert hat, ersetzt es nie.

### Aus dem Quelltext

```sh
pip install --user .            # braucht Python 3.11+, PySide6, Pillow
pip install --user '.[keyring]' # optional: das Passwort im System-Keyring
fritzfax-gui                    # Programm
fritzfax --help                 # Kommandozeile
```

## Auf der FRITZ!Box

* Telefonie → Telefoniegeräte: Die Faxfunktion muss mit einer Faxnummer eingerichtet sein.
* Einen Box-Benutzer mit der Berechtigung für Telefonie nutzen („Sprachnachrichten, Faxnachrichten, …“).
* **Mit Vorwahl** wählen: ohne (und bei Auslandsnummern) verlangt die Box eine Bestätigung an der Box.

## Kommandozeile

```sh
fritzfax trust                                   # Zertifikat der Box pinnen (erneut, wenn die Box es erneuert hat)
fritzfax set-password                            # Passwort in den Keyring (oder FRITZFAX_PASSWORD)
fritzfax info
fritzfax send --to "0531 49059113" brief.pdf     # die PDF-Seiten sind das Fax
fritzfax send --to "030 1234567, 040 7654321" --text "Hallo" --subject "Antrag" --cover anlage.pdf
fritzfax send --to 0301234567 --dry-run --out vorschau/ brief.pdf
fritzfax journal [--download ID --out fax.pdf] [--delete ID]
fritzfax boxconfig [--identifier X --header Y --email a@b.de --no-email-forward]
```

Einstellungen und Sendeprotokoll: `~/.config/fritzfax` und `~/.local/share/fritzfax` unter Linux,
`%APPDATA%\fritzfax` und `%LOCALAPPDATA%\fritzfax` unter Windows, `~/Library/Application Support/fritzfax` unter macOS.

## Entwicklung

Tests und der Linux-Build laufen in wegwerfbaren podman-Containern; Windows und macOS baut
[.github/workflows/release.yml](.github/workflows/release.yml).

```sh
tools/dev.sh build        # einmal (braucht Netz)
tools/dev.sh pytest       # Unit, Integration (simulierte FRITZ!Box), GUI (offscreen), offline
FRITZFAX_URL=https://<Box-Adresse> FRITZFAX_USER=… FRITZFAX_PASSWORD=… tools/dev.sh live
                          # nur lesende Prüfungen gegen eine echte Box (+ FRITZFAX_LIVE_SEND=<Nr> sendet EIN Fax)
./dist.sh                 # Linux-Pakete (zuerst Privacy-Scan, alles offline in podman)
tools/dist_test.sh --gui  # die Pakete auf Debian, Ubuntu, Fedora, Arch, Gentoo und unter X11
```

## Lizenz und Marken

MIT, siehe [LICENSE](LICENSE). Mitgeliefertes LibreOffice, Qt und die Python-Bibliotheken behalten
ihre eigenen Lizenzen (LICENSES/ in den Paketen). FRITZ!Box ist eine Marke von AVM; fritzfax steht
in keiner Verbindung zu AVM. Der Deutschland-Umriss der Animation stammt aus Natural Earth
(gemeinfrei); ihr Adler ist frei gestaltet und nicht das Bundeswappen.
