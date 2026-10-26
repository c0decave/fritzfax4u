"""Error types. Every message is English; the GUI translates by type."""
from __future__ import annotations


class BoxError(Exception):
    """Base class for everything that can go wrong talking to the box."""


class ConnectionFailed(BoxError):
    pass


class BoxNotConfigured(BoxError):
    def __init__(self, message: str = "no FRITZ!Box address configured: enter it in the settings or pass --url"):
        super().__init__(message)


class InvalidBoxAddress(BoxError):
    def __init__(self, address: str):
        super().__init__(f"not a valid FRITZ!Box address: {address!r} (use the address you open in the "
                         "browser, e.g. https://fritz.box)")
        self.address = address


class ProtocolError(BoxError):
    """The box answered something this client does not understand."""


class LoginFailed(BoxError):
    def __init__(self, message: str = "login failed: wrong user name or password", block_time: int = 0):
        super().__init__(message)
        self.block_time = block_time


class LoginBlocked(BoxError):
    def __init__(self, seconds: int):
        super().__init__(f"login blocked by the box for {seconds} s after failed attempts")
        self.seconds = seconds


class SessionExpired(BoxError):
    pass


class UntrustedCertificate(BoxError):
    def __init__(self, fingerprint: str):
        super().__init__(f"the box presents an unknown TLS certificate (SHA-256 {fingerprint})")
        self.fingerprint = fingerprint


class CertificateMismatch(BoxError):
    def __init__(self, expected: str, actual: str):
        super().__init__(f"TLS certificate changed: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


class FaxNotConfigured(BoxError):
    pass


class MissingRight(BoxError):
    pass


class TwoFactorRequired(BoxError):
    """The box wants a second-factor confirmation before it carries out the action."""


class TwoFactorFailed(BoxError):
    pass


class UploadRejected(BoxError):
    def __init__(self, status_text: str):
        super().__init__(f"the box rejected the fax: {status_text}")
        self.status_text = status_text


class FaxFailed(BoxError):
    def __init__(self, message: str, reason: int = 0):
        super().__init__(message)
        self.reason = reason


class FaxResultUnknown(FaxFailed):
    """The box was still in the call when fritzfax stopped waiting: the fax may still go through."""

    def __init__(self, seconds: float):
        super().__init__(f"no result from the box after {seconds:.0f} s; the box may still be sending "
                         "(result unknown, see the fax journal)")
        self.seconds = seconds


class ValidationFailed(BoxError):
    def __init__(self, field: str, alert: str):
        super().__init__(f"{field}: {alert}")
        self.field = field
        self.alert = alert


class FaxBusy(BoxError):
    """The box is already transmitting a fax; it handles one at a time."""
