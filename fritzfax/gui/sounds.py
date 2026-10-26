"""Sounds of the send animation, synthesized here (no recordings, no licences) and played by the system.

fax_sound(): the handshake people know from fax machines, after ITU-T T.30: the calling tone CNG
(1100 Hz), the answer tone CED (2100 Hz, with phase reversals like a modem answer), V.21 flags
(1650/1850 Hz) and the hiss of the fast training. fanfare(): a short trumpet call.

Playing uses the system's player (pw-play, paplay or aplay; afplay on macOS; winsound on Windows),
started with the system's libraries (syslibs.system_env). Without one the animation stays silent.
"""
from __future__ import annotations

import array
import math
import random
import shutil
import subprocess
import sys
import wave
from pathlib import Path

from .. import syslibs

RATE = 16000
_TWO_PI = 2 * math.pi


class _Tone:
    """Builds a signal from segments with continuous phase."""

    def __init__(self):
        self.data: list[float] = []
        self.phase = 0.0

    def tone(self, freq: float, seconds: float, volume: float = 0.3, reversals: float = 0.0) -> None:
        n = round(seconds * RATE)
        flip_every = round(reversals * RATE) if reversals else 0
        for i in range(n):
            if flip_every and i and i % flip_every == 0:
                self.phase += math.pi
            self.phase += _TWO_PI * freq / RATE
            ramp = min(1.0, i / (0.01 * RATE), (n - i) / (0.01 * RATE))  # no clicks
            self.data.append(volume * ramp * math.sin(self.phase))

    def silence(self, seconds: float) -> None:
        self.data.extend([0.0] * round(seconds * RATE))

    def fsk(self, bits: list[int], baud: float, mark: float, space: float, volume: float = 0.28) -> None:
        per_bit = RATE / baud
        for i in range(round(len(bits) * per_bit)):
            freq = mark if bits[int(i / per_bit)] else space
            self.phase += _TWO_PI * freq / RATE
            self.data.append(volume * math.sin(self.phase))

    def hiss(self, seconds: float, volume: float = 0.18, seed: int = 4) -> None:
        rnd = random.Random(seed)
        prev = 0.0
        for i in range(round(seconds * RATE)):
            # band-limited noise with a carrier, like the training of a fast fax modem
            white = rnd.uniform(-1, 1)
            prev = 0.6 * prev + 0.4 * white
            self.data.append(volume * (0.7 * prev + 0.3 * math.sin(_TWO_PI * 1800 * i / RATE)))


def _write(path: Path, data: list[float]) -> float:
    peak = max(1e-9, max(abs(v) for v in data))
    scale = min(1.0, 0.95 / peak)  # never clip
    pcm = array.array("h", (round(v * scale * 32767) for v in data))
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)
    return len(data) / RATE


def _duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def fax_sound(directory: Path) -> tuple[Path, float]:
    path = Path(directory) / "fritzfax-fax.wav"
    if path.exists():
        return path, _duration(path)
    t = _Tone()
    t.tone(1100, 0.5, 0.22)                      # CNG
    t.silence(0.5)
    t.tone(2100, 2.0, 0.24, reversals=0.45)      # CED / ANSam-like answer
    t.silence(0.15)
    flags = [0, 1, 1, 1, 1, 1, 1, 0] * 34         # HDLC flags at 300 bit/s
    t.fsk(flags, 300, 1650, 1850)
    t.silence(0.1)
    t.hiss(1.3)                                   # training of the fast modem
    t.silence(0.2)
    return path, _write(path, t.data)


def _trumpet(freq: float, seconds: float, volume: float) -> list[float]:
    out = []
    n = round(seconds * RATE)
    for i in range(n):
        t = i / RATE
        attack = min(1.0, t / 0.035)
        release = min(1.0, (n - i) / (0.06 * RATE))
        vibrato = 1 + (0.006 * math.sin(_TWO_PI * 5.5 * t) if t > 0.15 else 0)
        bright = 0.55 + 0.45 * attack  # brass gets brighter as the note opens
        v = sum((bright ** (k - 1)) / k ** 0.7 * math.sin(_TWO_PI * freq * k * vibrato * t) for k in range(1, 9))
        out.append(volume * attack * release * v)
    return out


def fanfare(directory: Path) -> tuple[Path, float]:
    path = Path(directory) / "fritzfax-fanfare.wav"
    if path.exists():
        return path, _duration(path)
    g4, c5, e5, g5 = 392.0, 523.25, 659.25, 783.99
    data: list[float] = []
    for freq, seconds in ((g4, 0.16), (c5, 0.16), (e5, 0.16), (g5, 0.42), (e5, 0.16), (g5, 0.9)):
        data += _trumpet(freq, seconds, 0.12)
        data += [0.0] * round(0.035 * RATE)
    # a second trumpet a third below on the last note
    tail = _trumpet(e5, 0.9, 0.08)
    start = len(data) - round((0.9 + 0.035) * RATE)
    for i, v in enumerate(tail):
        data[start + i] += v
    data += [0.0] * round(0.2 * RATE)
    return path, _write(path, data)


def player_command(path: str) -> list[str] | None:
    """The system's command that plays a WAV file, None if there is none."""
    if sys.platform == "darwin":
        candidates = [("afplay", [])]
    else:
        candidates = [("pw-play", []), ("paplay", []), ("aplay", ["-q"])]
    for name, args in candidates:
        exe = shutil.which(name)
        if exe:
            return [exe, *args, str(path)]
    return None


class Player:
    """Plays one sound at a time; never raises (no player, no device: silence)."""

    def __init__(self):
        self.proc = None

    def play(self, path: Path) -> None:
        self.stop()
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:  # noqa: BLE001 - no sound device
                pass
            return
        cmd = player_command(str(path))
        if cmd is None:
            return
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL, env=syslibs.system_env())
        except OSError:
            self.proc = None

    def stop(self) -> None:
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(None, 0)
            except Exception:  # noqa: BLE001
                pass
            return
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
