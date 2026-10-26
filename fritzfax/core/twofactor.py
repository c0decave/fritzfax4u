"""Second-factor confirmation via /twofactor.lua (FRITZ!OS 8.25, js/twofactor.js).

tfa_start answers {"state": "button,dtmf;4711"}: the methods offered plus a code that the
user dials as ``*1<code>`` on a box phone. Alternatively the user presses a button on the
box, or enters a TOTP code (Google Authenticator) if configured. tfa_active is polled until
{"done": true, "active": true}.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .errors import TwoFactorFailed
from .session import BoxClient

ENDPOINT = "/twofactor.lua"
START_ERRORS = {"91": "another session is already confirming an action",
                "92": "too many attempts, the box blocks confirmation for now"}


@dataclass
class Challenge:
    methods: set[str]
    code: str | None
    totp_available: bool = False

    @property
    def dial(self) -> str | None:
        return f"*1{self.code}" if self.code and "dtmf" in self.methods else None


def parse_state(state: str) -> Challenge:
    methods_part, _, code = (state or "").partition(";")
    methods = {m for m in methods_part.split(",") if m}
    return Challenge(methods, code or None)


def start(client: BoxClient) -> Challenge:
    answer = client.post(ENDPOINT, {"tfa_start": ""}).json()
    state = answer.get("state", "")
    if state.startswith("starterror"):
        code = state.partition(";")[2]
        raise TwoFactorFailed(START_ERRORS.get(code, f"confirmation could not be started (error {code})"))
    challenge = parse_state(state)
    ga = answer.get("googleauth") or {}
    challenge.totp_available = bool(ga.get("isAvailable") and ga.get("isConfigured"))
    if not challenge.methods and not challenge.totp_available:
        raise TwoFactorFailed(f"box offered no confirmation method ({state!r})")
    return challenge


def poll(client: BoxClient) -> tuple[bool, bool]:
    answer = client.post(ENDPOINT, {"tfa_active": ""}).json()
    return bool(answer.get("done")), bool(answer.get("active"))


def submit_totp(client: BoxClient, code: str) -> bool:
    return client.post(ENDPOINT, {"tfa_googleauth": code}).json().get("err") == 0


def cancel(client: BoxClient) -> None:
    client.post(ENDPOINT, {"tfa_cancel": ""})


def wait_confirmed(client: BoxClient, *, timeout: float = 120.0, poll_interval: float = 1.0,
                   should_cancel: Callable[[], bool] | None = None,
                   clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
    started = clock()
    while True:
        if should_cancel and should_cancel():
            cancel(client)
            raise TwoFactorFailed("confirmation cancelled by the user")
        done, active = poll(client)
        if done:
            if active:
                return
            raise TwoFactorFailed("the box rejected the confirmation")
        if clock() - started > timeout:
            cancel(client)
            raise TwoFactorFailed("confirmation timed out")
        sleep(poll_interval)


def confirm(client: BoxClient, on_challenge: Callable[[Challenge], None], **kw) -> None:
    """Start a confirmation, show it to the user, and wait until it is done."""
    challenge = start(client)
    on_challenge(challenge)
    wait_confirmed(client, **kw)
