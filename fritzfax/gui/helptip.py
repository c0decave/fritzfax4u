"""Small (?) buttons that explain a field.

Hovering the button (or the field) shows the explanation; clicking it or pressing Space on it
shows it too, so it also works without a mouse. All texts live here, in one translation
context ("Help"), so the same explanation reads the same in the settings and in the main window.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLayout, QToolButton, QToolTip, QWidget

from ..core import faxapi, job, paths


def tr(text: str) -> str:
    return QCoreApplication.translate("Help", text)


def fill_budgets(text: str) -> str:
    """Put the time budgets the send code uses into a help text (%1 s per page, %2 min of grace)."""
    return text.replace("%1", str(job.SECONDS_PER_PAGE)).replace("%2", f"{faxapi.OVERDUE_GRACE / 60:g}")


_FILL = {
    "stall_timeout": fill_budgets,
    "history": lambda text: text.replace("%1", str(paths.data_dir())),
}


def fill(key: str, text: str) -> str:
    """Put the values of this system and of the code into the help text of `key`."""
    return _FILL.get(key, lambda t: t)(text)


def texts(raw: bool = False) -> dict[str, str]:
    """Explanation per key, translated into the current UI language (raw: before fill())."""
    templates = _templates()
    return templates if raw else {key: fill(key, text) for key, text in templates.items()}


def _templates() -> dict[str, str]:
    mode = tr("Document: the files you add are sent as they are, page by page - for example a signed PDF. "
              "Letter: fritzfax writes your text on the first page(s) and puts the files after it.")
    cover = tr("A cover sheet is an extra first page with recipient, sender, both fax numbers, date, "
               "number of pages and subject.")
    header = tr("The header line is a thin line at the very top of every page with date, sender name, "
                "fax number and page n/N, like fax machines print it. 'On top' writes it over the upper "
                "edge of the page; 'page shrunk' makes the page a little smaller so nothing is covered.")
    return {
        # settings: connection
        "box_url": tr("The address you use in the browser to open your FRITZ!Box, for example "
                      "https://fritz.box. fritzfax has no built-in address."),
        "username": tr("The FRITZ!Box user fritzfax logs in with. Leave it empty if your box only asks for a "
                       "password; then the user last logged in at the box is used."),
        "keyring": tr("Keeps the password so you do not have to type it again: in the password storage of "
                      "your desktop (keyring), or, where there is none, in the settings file, which only your "
                      "user account can read. Without the tick the password is asked for once per session."),
        "certificate": tr("The FRITZ!Box uses its own (self-signed) certificate. When you trust it once, "
                          "fritzfax remembers its SHA-256 fingerprint and refuses to connect if it ever "
                          "changes - a protection against someone listening in between. You can compare it "
                          "with the certificate your browser shows for the box."),
        "timeout": tr("How long fritzfax waits for the box to answer a single request before it reports "
                      "that the box cannot be reached."),
        # settings: sender
        "sender_name": tr("Your name or company. fritzfax prints it in the header line of every page and on "
                          "the cover sheet. You can change it for a single fax in the main window."),
        "sender_number": tr("The fax number of your box that faxes are sent from. Leave it empty to use the "
                            "first fax number set up in the box."),
        "shown_number": tr("The fax number printed on your faxes (header line and cover sheet), for example the "
                           "number replies should go to. Leave it empty to print the number the fax is sent "
                           "from. It is only printed: the caller number the recipient's line shows is the one "
                           "the box sends from, and the station id is a setting of the box (tab 'Box fax "
                           "settings')."),
        "country_code": tr("Your country's dialing code, written with + (+49 for Germany). A number entered "
                           "as +49... is dialed as 0..., any other +... number with 00 in front."),
        # settings: sending
        "mode": mode,
        "cover": cover,
        "header": header,
        "dither": tr("Faxes are black and white only. 'Text and line art' gives sharp letters; 'Photos' "
                     "imitates grey with fine dot patterns; 'Automatic' decides per page."),
        "max_pages": tr("Safety limit: fritzfax refuses to send a fax with more pages than this, for example "
                        "when a wrong file was chosen by mistake."),
        "start_timeout": tr("How long fritzfax waits for the box to start dialing. If the box has not started "
                            "by then, the fax counts as failed."),
        "stall_timeout": tr(
            "How long a transmission may take, at least %1 seconds per page. If the box is still in the call "
            "after that, fritzfax says so and waits up to %2 more minutes for the result of the box. Cancel "
            "stops waiting at any time."),
        # settings: documents
        "soffice": tr("Office documents (Word, Excel, OpenDocument ...) are converted with LibreOffice. Leave "
                      "it empty to use the LibreOffice installed on the system or the one included with "
                      "fritzfax. Without LibreOffice you can still send PDF files and images."),
        "font": tr("Font for the letter text and the cover sheet. Leave it empty to use DejaVu Sans or "
                   "another font found on the system."),
        # settings: box fax settings
        "box_identifier": tr("The station id: a short text, usually your fax number, that the receiving fax "
                             "machine shows and prints in its transmission report (at most 20 characters)."),
        "box_header": tr("The header text the FRITZ!Box keeps for its own fax functions. The header line of "
                         "faxes sent with fritzfax is set under Sending - Header line."),
        "box_sender": tr("The sender name the FRITZ!Box keeps for its own fax functions. Faxes sent with "
                         "fritzfax use the sender name from the tab Sender."),
        "box_email": tr("The box sends every received fax as a PDF to this e-mail address. This needs the "
                        "push service of the box to be set up; empty uses its address."),
        "box_store": tr("The box keeps received faxes in its internal memory, where you find them in the fax "
                        "journal (menu Box - Fax journal)."),
        "box_send_only": tr("The box no longer answers incoming fax calls; it only sends faxes."),
        # settings: interface
        "theme": tr("System follows the look of your desktop; Light and Dark choose it for fritzfax. The "
                    "change is visible at once."),
        "animation": tr("While a fax is sent: Germany in black, red and gold with an eagle and a fax machine, "
                        "the fax sound and a fanfare when the fax went through. A click closes it. Off unless "
                        "you switch it on."),
        "history": tr("Records every fax you send: when, to which number, what content, the result and the "
                      "check with the box, plus a PDF copy of the pages as sent. It stays on this computer "
                      "(%1)."),
        # main window
        "to_numbers": tr("One or more fax numbers, separated by comma; several numbers are sent one after "
                         "another. Spaces, dashes and slashes are allowed, and +49... works too."),
        "to_name": tr("The name of the recipient. It is only printed on the cover sheet."),
        "from_name": tr("Printed in the header line and on the cover sheet. The default comes from the "
                        "settings (tab Sender)."),
        "from_number": tr("The fax number of your box the fax is sent from. The list is filled from the box "
                          "when fritzfax connects."),
        "files": tr("PDF files, images and office documents. Drop files here or use Add. The order of the "
                    "list is the order in the fax; double-click a file to send only some of its pages."),
        "preview": tr("Every page exactly as it is sent. To leave pages out, select them and press Del, or "
                      "right click them; fritzfax asks first. Removing the cover sheet turns it off; pages "
                      "of the letter text are changed by changing the text."),
    }


def rich(text: str) -> str:
    """Tooltip markup: escaped text in a paragraph, so Qt wraps long lines and shows < and & as typed."""
    return "<p>" + html.escape(text, quote=False).replace("\n", "<br>") + "</p>"


# a small round badge; palette() colors follow the light and dark theme
BADGE_STYLE = ("QToolButton { border: 1px solid palette(mid); border-radius: 9px; padding: 0; "
               "min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px; font-weight: bold; } "
               "QToolButton:hover, QToolButton:focus { border-color: palette(highlight); }")


class HelpButton(QToolButton):
    """The (?) next to a field."""

    def __init__(self, text: str, parent: QWidget | None = None):
        if not text or not text.strip():
            raise ValueError("help text must not be empty")
        super().__init__(parent)
        self.help_text = text
        self.setText("?")
        self.setAutoRaise(True)
        self.setStyleSheet(BADGE_STYLE)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setToolTip(rich(text))
        self.setWhatsThis(rich(text))
        self.setAccessibleDescription(text)
        self.clicked.connect(self.show_help)

    def show_help(self) -> None:
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.toolTip(), self)


def with_help(owner, key: str, field: QWidget | QLayout, text: str | None = None,
              compact: bool | None = None) -> QWidget:
    """Return a row with `field` and a (?) button; the button is kept in owner.help[key].

    `text` defaults to the shared explanation texts()[key]. A compact row keeps the (?) right
    after the field (default for check boxes); otherwise the field fills the row."""
    registry = owner.__dict__.setdefault("help", {})
    if key in registry:
        raise ValueError(f"help for {key!r} added twice")
    button = HelpButton(texts()[key] if text is None else text)
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    # a check box keeps its (?) right after its text; other fields fill the row, (?) at its end
    if compact is None:
        compact = isinstance(field, QAbstractButton)
    if isinstance(field, QLayout):
        layout.addLayout(field, 0 if compact else 1)
    else:
        layout.addWidget(field, 0 if compact else 1)
        if not field.toolTip():
            field.setToolTip(button.toolTip())
    layout.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
    if compact:
        layout.addStretch(1)
    registry[key] = button
    return row
