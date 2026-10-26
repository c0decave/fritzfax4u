"""Settings in fritzfax.conf (INI) in paths.config_dir(): ~/.config/fritzfax on Linux. The password
never goes into the file: it comes from $FRITZFAX_PASSWORD, the system keyring, or is asked for per
session."""
from __future__ import annotations

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


class NoKeyring(RuntimeError):
    pass


@dataclass
class Settings:
    box_url: str = ""  # no built-in address: every user enters their own box
    username: str = ""
    cert_sha256: str = ""
    timeout: float = 20.0
    sender_name: str = ""
    sender_number: str = ""
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
        shutil.copyfile(path, backup)
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
    return settings


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
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
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
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, _account(settings))
    except Exception:  # noqa: BLE001
        return None


def delete_password(settings: Settings) -> None:
    """Remove a stored password; no keyring or no entry is not an error."""
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, _account(settings))
    except Exception:  # noqa: BLE001 - keyring.errors.PasswordDeleteError when there is none
        pass


def set_password(settings: Settings, password: str) -> None:
    kr = _keyring()
    if kr is None:
        raise NoKeyring("no system keyring available")
    kr.set_password(SERVICE, _account(settings), password)
