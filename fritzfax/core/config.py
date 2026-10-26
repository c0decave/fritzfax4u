"""Settings in fritzfax.conf (INI) in paths.config_dir(): ~/.config/fritzfax on Linux.

The password comes from (in this order) the current session, $FRITZFAX_PASSWORD, the system keyring,
or the settings file. It goes into the settings file only when the user asked to save it and there is
no usable keyring. There it is base64 encoded, which is NOT encryption: it only keeps the password from
being read at a glance. The protection is the file mode 600 (only the own user account can read it)."""
from __future__ import annotations

import base64
import binascii
import configparser
import os
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import paths

SERVICE = "fritzfax"
HEADER_MODES = ("overlay", "shrink", "off")
DITHER_MODES = ("auto", "threshold", "dither")
SEND_MODES = ("document", "letter")
LANGUAGES = ("auto", "de", "en")
THEMES = ("system", "light", "dark")
MAX_RECENT = 10

# field name -> (section, option)
_LAYOUT = {
    "box_url": ("box", "url"), "username": ("box", "username"), "cert_sha256": ("box", "cert_sha256"),
    "timeout": ("box", "timeout"),
    "sender_name": ("sender", "name"), "sender_number": ("sender", "number"),
    "shown_number": ("sender", "shown_number"),
    "default_mode": ("send", "default_mode"), "cover": ("send", "cover"), "header_mode": ("send", "header_mode"),
    "dither": ("send", "dither"), "max_pages": ("send", "max_pages"), "start_timeout": ("send", "start_timeout"),
    "stall_timeout": ("send", "stall_timeout"), "country_code": ("send", "country_code"),
    "poll_interval": ("send", "poll_interval"),
    "soffice": ("documents", "soffice"), "font_file": ("documents", "font_file"), "font_size": ("documents", "font_size"),
    "language": ("ui", "language"), "recent_numbers": ("ui", "recent_numbers"), "keep_history": ("ui", "keep_history"),
    "animation": ("ui", "animation"),
    "theme": ("ui", "theme"),
}
_CHOICES = {"header_mode": HEADER_MODES, "dither": DITHER_MODES, "default_mode": SEND_MODES, "language": LANGUAGES, "theme": THEMES}


@dataclass
class Settings:
    box_url: str = ""  # no built-in address: every user enters their own box
    username: str = ""
    cert_sha256: str = ""
    timeout: float = 20.0
    sender_name: str = ""
    sender_number: str = ""
    shown_number: str = ""  # printed on the fax instead of sender_number (header line, cover); "" = sender_number
    default_mode: str = "document"
    cover: bool = False
    header_mode: str = "overlay"
    dither: str = "auto"
    max_pages: int = 50
    start_timeout: float = 60.0
    stall_timeout: float = 180.0
    poll_interval: float = 1.0
    country_code: str = "49"
    soffice: str = ""
    font_file: str = ""
    font_size: float = 11.0
    language: str = "de"  # the UI and the transmission report are German unless chosen otherwise
    recent_numbers: list[str] = field(default_factory=list)
    keep_history: bool = True
    animation: bool = False  # Germany, eagle, fax machine, fax sound and fanfare while sending
    load_problem: str = field(default="", repr=False, compare=False)  # not stored: why load() used defaults
    load_backup: str = field(default="", repr=False, compare=False)  # ... and where the old file was kept
    theme: str = "system"
    # the saved password when there is no keyring (see set_password) and the account it belongs to
    saved_password: str = field(default="", repr=False)
    saved_password_for: str = field(default="", repr=False)

    def __post_init__(self):
        self.password: str | None = None  # memory only, see get_password()
        self.keyring_pending = False  # store self.password in the keyring once the box accepted it

    def __eq__(self, other):
        return isinstance(other, Settings) and all(
            getattr(self, f.name) == getattr(other, f.name) for f in fields(self) if f.compare)

    def remember_number(self, number: str) -> None:
        self.recent_numbers = [number] + [n for n in self.recent_numbers if n != number]
        del self.recent_numbers[MAX_RECENT:]


def default_path() -> Path:
    return paths.config_dir() / "fritzfax.conf"


def _convert(name: str, raw: str, default):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if name in _CHOICES and raw not in _CHOICES[name]:
        raise ValueError(raw)
    return raw


def load(path: Path | None = None) -> Settings:
    path = Path(path or default_path())
    settings = Settings()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as exc:
        # start with the defaults, but keep the old file: the next save would overwrite it
        backup = path.with_name(path.name + ".broken")
        _private_copy(path, backup)  # it may hold the saved password
        reason = str(exc).splitlines()[0]
        settings.load_problem = (f"{path} could not be read ({reason}); default settings are used. "
                                 f"The old file was kept as {backup}.")
        settings.load_backup = str(backup)
        return settings
    for name, (section, option) in _LAYOUT.items():
        if parser.has_option(section, option):
            try:
                setattr(settings, name, _convert(name, parser.get(section, option), getattr(settings, name)))
            except ValueError:
                pass  # keep the default for a value we do not understand
    if parser.has_option("box", "password_b64"):
        try:
            settings.saved_password = base64.b64decode(parser.get("box", "password_b64"), validate=True).decode("utf-8")
            settings.saved_password_for = parser.get("box", "password_for", fallback="")
        except (binascii.Error, UnicodeDecodeError):
            pass  # an unreadable saved password is asked for again
    return settings


def _private_copy(src: Path, dst: Path) -> None:
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        _make_private(fh.fileno())
        fh.write(src.read_bytes())


def _make_private(fd: int) -> None:
    """os.open's mode only applies to a NEW file: an old one keeps its mode."""
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)


def save(settings: Settings, path: Path | None = None) -> Path:
    path = Path(path or default_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser(interpolation=None)
    for name, (section, option) in _LAYOUT.items():
        value = getattr(settings, name)
        if isinstance(value, list):
            value = ", ".join(value)
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, option, str(value))
    if settings.saved_password:
        parser.set("box", "password_for", settings.saved_password_for)
        parser.set("box", "password_b64", base64.b64encode(settings.saved_password.encode("utf-8")).decode("ascii"))
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        _make_private(fh.fileno())
        parser.write(fh)
    os.replace(tmp, path)
    return path


def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - any import/backend problem means "no keyring"
        return None


def _account(settings: Settings) -> str:
    return f"{settings.username}@{settings.box_url}"


def get_password(settings: Settings) -> str | None:
    if settings.password:
        return settings.password
    env = os.environ.get("FRITZFAX_PASSWORD")
    if env:
        return env
    kr = _keyring()
    if kr is not None:
        try:
            stored = kr.get_password(SERVICE, _account(settings))
        except Exception:  # noqa: BLE001 - a locked wallet: try the settings file
            stored = None
        if stored:
            return stored
    if settings.saved_password and settings.saved_password_for == _account(settings):
        return settings.saved_password
    return None


def delete_password(settings: Settings) -> None:
    """Remove a stored password; no keyring or no entry is not an error. The caller saves the settings."""
    settings.saved_password = settings.saved_password_for = ""
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, _account(settings))
    except Exception:  # noqa: BLE001 - keyring.errors.PasswordDeleteError when there is none
        pass


def set_password(settings: Settings, password: str) -> str:
    """Keep the password for the next start: "keyring", or "file" when there is no usable keyring.

    "file" only sets it in `settings`: the caller saves them (save()), and should tell the user."""
    if not password:
        raise ValueError("an empty password is not stored")
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, _account(settings), password)
        except Exception:  # noqa: BLE001 - the wallet stayed locked, no backend ...: use the file
            pass
        else:
            settings.saved_password = settings.saved_password_for = ""  # one place only
            return "keyring"
    settings.saved_password, settings.saved_password_for = password, _account(settings)
    return "file"
