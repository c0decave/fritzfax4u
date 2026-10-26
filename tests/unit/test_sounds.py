"""Fax sound and fanfare of the send animation: synthesized, valid WAV, played by the system."""
import array
import wave

import pytest

from fritzfax.gui import sounds


def samples(path):
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, sounds.RATE)
        return array.array("h", w.readframes(w.getnframes())), w.getnframes() / w.getframerate()


def frequency(data, start, seconds):
    part = data[int(start * sounds.RATE):int((start + seconds) * sounds.RATE)]
    crossings = sum(1 for a, b in zip(part, part[1:]) if (a < 0) != (b < 0))
    return crossings / 2 / seconds


def test_fax_sound_follows_the_real_handshake(tmp_path):
    path, seconds = sounds.fax_sound(tmp_path)
    data, length = samples(path)
    assert abs(length - seconds) < 0.01 and 4 < seconds < 7
    assert abs(frequency(data, 0.05, 0.4) - 1100) < 30   # CNG: the calling fax
    assert abs(frequency(data, 1.2, 0.3) - 2100) < 40    # CED: the answering fax
    assert max(abs(v) for v in data[int(0.6 * sounds.RATE):int(0.9 * sounds.RATE)]) < 50  # pause


def test_fanfare_is_loud_enough_and_never_clips(tmp_path):
    path, seconds = sounds.fanfare(tmp_path)
    data, length = samples(path)
    assert abs(length - seconds) < 0.01 and 1.5 < seconds < 4
    peak = max(abs(v) for v in data)
    assert 8000 < peak < 32767


def test_sounds_are_made_once(tmp_path):
    first = sounds.fax_sound(tmp_path)
    mtime = first[0].stat().st_mtime_ns
    assert sounds.fax_sound(tmp_path) == first and first[0].stat().st_mtime_ns == mtime


# --- playing them ---------------------------------------------------------------------------------

@pytest.mark.parametrize("available, expected", [
    ({"pw-play", "paplay", "aplay"}, "pw-play"), ({"paplay", "aplay"}, "paplay"), ({"aplay"}, "aplay"), (set(), None)])
def test_linux_player_choice(monkeypatch, available, expected):
    monkeypatch.setattr(sounds.sys, "platform", "linux")
    monkeypatch.setattr(sounds.shutil, "which", lambda name: f"/usr/bin/{name}" if name in available else None)
    cmd = sounds.player_command("/x/fax.wav")
    assert (cmd[0].rsplit("/", 1)[-1] if cmd else None) == expected
    if cmd:
        assert cmd[-1] == "/x/fax.wav"


def test_mac_uses_afplay(monkeypatch):
    monkeypatch.setattr(sounds.sys, "platform", "darwin")
    monkeypatch.setattr(sounds.shutil, "which", lambda name: "/usr/bin/afplay" if name == "afplay" else None)
    assert sounds.player_command("/x/a.wav") == ["/usr/bin/afplay", "/x/a.wav"]


def test_player_runs_with_the_system_libraries_and_can_be_stopped(monkeypatch, tmp_path):
    started = []

    class Proc:
        def __init__(self, cmd, **kw):
            started.append((cmd, kw))
            self.killed = False

        def poll(self):
            return 0 if self.killed else None

        def terminate(self):
            self.killed = True

    monkeypatch.setattr(sounds.sys, "platform", "linux")
    monkeypatch.setattr(sounds.shutil, "which", lambda name: "/usr/bin/aplay" if name == "aplay" else None)
    monkeypatch.setattr(sounds.subprocess, "Popen", Proc)
    monkeypatch.setattr(sounds.syslibs, "system_env", lambda: {"PATH": "/usr/bin"})
    player = sounds.Player()
    player.play(tmp_path / "a.wav")
    (cmd, kw), = started
    assert kw["env"] == {"PATH": "/usr/bin"} and kw["stdout"] == sounds.subprocess.DEVNULL
    player.stop()
    assert player.proc.killed


def test_player_without_any_program_stays_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(sounds.sys, "platform", "linux")
    monkeypatch.setattr(sounds.shutil, "which", lambda name: None)
    player = sounds.Player()
    player.play(tmp_path / "a.wav")  # no error, nothing to hear
    player.stop()


def test_player_that_cannot_start_stays_silent(monkeypatch, tmp_path):
    def fail(*a, **k):
        raise OSError("no audio")

    monkeypatch.setattr(sounds.sys, "platform", "linux")
    monkeypatch.setattr(sounds.shutil, "which", lambda name: "/usr/bin/aplay")
    monkeypatch.setattr(sounds.subprocess, "Popen", fail)
    sounds.Player().play(tmp_path / "a.wav")
