fritzfax for macOS (Apple silicon) - send faxes through an AVM FRITZ!Box
==========================================================================

Move fritzfax.app to Applications. The app is not notarized by Apple: the first time,
right-click (or Control-click) fritzfax.app, choose "Open", then "Open" again.
If macOS says the app is damaged, run once in Terminal:
    xattr -dr com.apple.quarantine /Applications/fritzfax.app
Command line:  /Applications/fritzfax.app/Contents/MacOS/fritzfax --help
Check:         /Applications/fritzfax.app/Contents/MacOS/fritzfax selftest
Office documents need LibreOffice (https://www.libreoffice.org); PDF and images work without it.
Settings and send log: ~/Library/Application Support/fritzfax

----

fritzfax fuer macOS (Apple Silicon) - Faxe ueber eine AVM FRITZ!Box senden

fritzfax.app in den Ordner Programme ziehen. Die App ist nicht von Apple beglaubigt: beim ersten
Mal fritzfax.app mit rechter Maustaste (oder Ctrl-Klick) anklicken, "Oeffnen" waehlen, dann
nochmals "Oeffnen". Meldet macOS, die App sei beschaedigt, einmal im Terminal ausfuehren:
    xattr -dr com.apple.quarantine /Applications/fritzfax.app
Office-Dokumente brauchen LibreOffice (https://de.libreoffice.org); PDF und Bilder gehen ohne.
