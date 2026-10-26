"""The optional send animation: Germany in black, red and gold, eagle and fax machine, sound, fanfare."""
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox, QWidget

from fritzfax.core import config
from fritzfax.gui import celebration, workers
from fritzfax.gui.main_window import MainWindow
from tests_helpers import one_page_pdf


class FakePlayer:
    def __init__(self):
        self.played, self.stopped = [], 0

    def play(self, path):
        self.played.append(path.name)

    def stop(self):
        self.stopped += 1


@pytest.fixture
def fast(monkeypatch, tmp_path):
    """Short sounds and a recording player."""
    player = FakePlayer()
    (tmp_path / "fritzfax-fax.wav").write_bytes(b"")
    (tmp_path / "fritzfax-fanfare.wav").write_bytes(b"")
    monkeypatch.setattr(celebration, "_sounds", lambda: ((tmp_path / "fritzfax-fax.wav", 0.4),
                                                        (tmp_path / "fritzfax-fanfare.wav", 0.3)))
    monkeypatch.setattr(celebration, "Player", lambda: player)
    return player


def overlay(qtbot):
    host = QWidget()
    host.resize(900, 700)
    qtbot.addWidget(host)
    host.show()
    return celebration.Celebration(host)


# --- the sequence ------------------------------------------------------------------------------

def test_success_plays_fax_sound_then_fanfare_and_closes(qtbot, fast):
    c = overlay(qtbot)
    c.start()
    assert c.isVisible() and fast.played == ["fritzfax-fax.wav"]
    c.outcome(True)  # the fax went through while the sound plays
    assert fast.played == ["fritzfax-fax.wav"]  # the fanfare waits for the end of the fax sound
    qtbot.waitUntil(lambda: fast.played == ["fritzfax-fax.wav", "fritzfax-fanfare.wav"], timeout=3000)
    qtbot.waitUntil(lambda: not c.isVisible(), timeout=5000)


def test_failure_never_gets_a_fanfare(qtbot, fast):
    c = overlay(qtbot)
    c.start()
    c.outcome(False)
    qtbot.waitUntil(lambda: not c.isVisible(), timeout=3000)
    assert fast.played == ["fritzfax-fax.wav"]


def test_result_after_the_fax_sound(qtbot, fast):
    c = overlay(qtbot)
    c.start()
    qtbot.wait(700)  # the fax sound is over, the box still sends
    assert c.isVisible() and fast.played == ["fritzfax-fax.wav"]
    c.outcome(True)
    assert fast.played[-1] == "fritzfax-fanfare.wav"


def test_click_closes_and_stops_the_sound(qtbot, fast):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    c = overlay(qtbot)
    c.start()
    QTest.mouseClick(c, Qt.MouseButton.LeftButton)
    assert not c.isVisible() and fast.stopped >= 1
    c.outcome(True)  # a late result after closing does nothing
    qtbot.wait(500)
    assert fast.played == ["fritzfax-fax.wav"]


def test_grows_once_and_shrinks_back(qtbot, fast):
    c = overlay(qtbot)
    values = []
    c.scale_changed.connect(values.append)
    c.start()
    qtbot.waitUntil(lambda: c.animation.state() == c.animation.State.Stopped, timeout=3000)
    assert max(values) > 1.1 and abs(values[-1] - 1.0) < 1e-6
    peak = values.index(max(values))
    assert all(a <= b + 1e-9 for a, b in zip(values[:peak], values[1:peak + 1]))  # one swell, no wobble


def test_paints_black_red_gold(qtbot, fast):
    c = overlay(qtbot)
    c.start()
    img = c.grab().toImage()
    colors = {img.pixelColor(x, y).name() for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}

    def near(target):
        t = QColor(target)
        return any(abs(QColor(c).red() - t.red()) < 25 and abs(QColor(c).green() - t.green()) < 25
                   and abs(QColor(c).blue() - t.blue()) < 25 for c in colors)

    assert near(celebration.BLACK) and near(celebration.RED) and near(celebration.GOLD)


# --- in the main window: off by default --------------------------------------------------------

def _send(qtbot, tmp_path, fakebox, **kw):
    s = config.Settings(box_url=fakebox.url, username=fakebox.state.username, poll_interval=0.02, **kw)
    s.password = fakebox.state.password
    w = MainWindow(s, tmp_path / "f.conf")
    qtbot.addWidget(w)
    w.to_edit.setText("0301")
    w.add_files([str(one_page_pdf(tmp_path / "a.pdf"))])
    qtbot.waitUntil(lambda: w.prepared is not None, timeout=20000)
    w.send()
    return w


@pytest.fixture
def answers(monkeypatch, qtbot):
    shown = []
    for kind in ("question", "information", "warning"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, kind=kind, **k: shown.append(kind)
                            or QMessageBox.StandardButton.Yes)
    yield shown
    workers.wait_all(10000)


def test_animation_is_off_by_default(qtbot, tmp_path, fakebox, fast, answers):
    assert config.Settings().animation is False
    w = _send(qtbot, tmp_path, fakebox)
    qtbot.waitUntil(lambda: "information" in answers, timeout=30000)
    assert w.celebration is None and fast.played == []


def test_animation_when_switched_on(qtbot, tmp_path, fakebox, fast, answers):
    w = _send(qtbot, tmp_path, fakebox, animation=True)
    qtbot.waitUntil(lambda: "information" in answers, timeout=30000)
    qtbot.waitUntil(lambda: fast.played == ["fritzfax-fax.wav", "fritzfax-fanfare.wav"], timeout=5000)


def test_animation_without_fanfare_when_the_send_fails(qtbot, tmp_path, fakebox_factory, fast, answers):
    box = fakebox_factory(scenario="reason:3490")
    w = _send(qtbot, tmp_path, box, animation=True)
    qtbot.waitUntil(lambda: "warning" in answers, timeout=30000)
    qtbot.wait(800)
    assert fast.played == ["fritzfax-fax.wav"]


def test_settings_switch_and_help(qtbot, tmp_path):
    from fritzfax.gui.settings_dialog import SettingsDialog

    s = config.Settings()
    dlg = SettingsDialog(s, None, tmp_path / "f.conf")
    qtbot.addWidget(dlg)
    assert not dlg.animation_check.isChecked() and "animation" in dlg.help
    dlg.animation_check.setChecked(True)
    dlg.save()
    assert s.animation is True and config.load(tmp_path / "f.conf").animation is True
