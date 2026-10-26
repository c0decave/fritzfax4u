"""The optional send animation (Settings - Interface, off by default).

While a fax is sent: Germany in black, red and gold with an eagle and a fax machine in the middle; it
swells once and shrinks back while the fax sound plays. When the fax sound is over and the fax went
through, a fanfare. A failed fax gets no fanfare. A click (or Esc) closes it at any time.

The eagle is drawn here as a free design: the official federal eagle may not be used without
permission (Paragraph 124 OWiG). The outline of Germany comes from Natural Earth (public domain).
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import germany, sounds
from .sounds import Player

BLACK, RED, GOLD = "#000000", "#dd0000", "#ffce00"  # the colors of the federal flag
SWELL = 1.18  # largest size relative to the start

_dir: list[Path] = []


def _sounds() -> tuple[tuple[Path, float], tuple[Path, float]]:
    if not _dir:
        _dir.append(Path(tempfile.mkdtemp(prefix="fritzfax-sound-")))
        atexit.register(shutil.rmtree, _dir[0], True)
    return sounds.fax_sound(_dir[0]), sounds.fanfare(_dir[0])


class Celebration(QWidget):
    scale_changed = Signal(float)
    finished = Signal()

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tr("Click to close"))
        self.hide()
        (self.fax_path, fax_seconds), (self.fanfare_path, fanfare_seconds) = _sounds()
        self.fax_ms, self.fanfare_ms = round(fax_seconds * 1000), round(fanfare_seconds * 1000)
        self.player = Player()
        self.scale = 1.0
        self.fax_done = False
        self.ok: bool | None = None
        self.closed = False
        self.message = self.tr("Sending the fax ...")
        self.animation = QVariantAnimation(self, startValue=1.0, endValue=1.0, duration=max(1, self.fax_ms))
        self.animation.setKeyValueAt(0.5, SWELL)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.animation.valueChanged.connect(self._set_scale)
        self.fax_timer = QTimer(self, singleShot=True, interval=self.fax_ms)
        self.fax_timer.timeout.connect(self._fax_over)
        self.end_timer = QTimer(self, singleShot=True, interval=self.fanfare_ms + 600)
        self.end_timer.timeout.connect(self.close_overlay)
        host.installEventFilter(self)  # follow the size of the window

    # --- sequence ------------------------------------------------------------------------------
    def start(self) -> None:
        self.setGeometry(self.host.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self.player.play(self.fax_path)
        self.animation.start()
        self.fax_timer.start()

    def outcome(self, ok: bool) -> None:
        """The result of the send; the fanfare waits for the end of the fax sound."""
        if self.closed or self.ok is not None:
            return
        self.ok = ok
        if self.fax_done:
            self._conclude()

    def _fax_over(self) -> None:
        self.fax_done = True
        if self.ok is not None:
            self._conclude()

    def _conclude(self) -> None:
        if self.closed:
            return
        if self.ok:
            self.message = self.tr("Fax sent!")
            self.update()
            self.player.play(self.fanfare_path)
            self.end_timer.start()
        else:
            self.close_overlay()

    def close_overlay(self) -> None:
        if self.closed:
            return
        self.closed = True
        for timer in (self.fax_timer, self.end_timer):
            timer.stop()
        self.animation.stop()
        self.player.stop()
        self.hide()
        self.finished.emit()

    # --- events ------------------------------------------------------------------------------
    def _set_scale(self, value) -> None:
        self.scale = float(value)
        self.scale_changed.emit(self.scale)
        self.update()

    def eventFilter(self, obj, event):
        if obj is self.host and event.type() == QEvent.Type.Resize and not self.closed:
            self.setGeometry(self.host.rect())
        return False

    def mousePressEvent(self, event):
        self.close_overlay()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.close_overlay()
        else:
            super().keyPressEvent(event)

    # --- drawing -----------------------------------------------------------------------------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(10, 12, 16, 185))
        size = min(self.width() / germany.WIDTH * 0.62, self.height() * 0.70) * self.scale
        left = (self.width() - size * germany.WIDTH) / 2
        top = (self.height() - size) / 2 - 18
        outline = QPainterPath()
        for ring in germany.RINGS:
            outline.moveTo(left + ring[0][0] * size, top + ring[0][1] * size)
            for x, y in ring[1:]:
                outline.lineTo(left + x * size, top + y * size)
            outline.closeSubpath()
        p.save()
        p.setClipPath(outline)
        for i, color in enumerate((BLACK, RED, GOLD)):
            p.fillRect(QRectF(left - 2, top + i * size / 3, size * germany.WIDTH + 4, size / 3 + 1), QColor(color))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 210), max(1.5, size / 300)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(outline)
        center = QPointF(left + size * germany.WIDTH * 0.53, top + size * 0.52)
        radius = size * 0.2
        p.setPen(QPen(QColor(GOLD), max(2.0, radius / 18)))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(center, radius, radius)
        _draw_eagle(p, center, radius)
        _draw_fax(p, QPointF(center.x(), center.y() + radius * 0.38), radius)
        font = QFont(self.font())
        font.setPointSizeF(max(12.0, size / 28))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, top + size + 8, self.width(), size / 10 + 30),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.message)
        p.end()


def _draw_eagle(p: QPainter, c: QPointF, r: float) -> None:
    """A free, stylized heraldic eagle in black: raised wings with feather tips, head turned to the side,
    a fanned tail. Not the federal eagle (see the module docstring)."""
    p.save()
    p.translate(c)
    p.scale(r / 100, r / 100)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BLACK))
    right_wing = [(16, -34), (34, -66), (54, -90), (60, -74), (76, -88), (78, -66), (93, -70), (88, -48),
                  (100, -46), (88, -28), (98, -18), (74, -8), (46, 0), (20, 6)]
    for side in (1, -1):
        wing = QPainterPath()
        wing.moveTo(side * right_wing[0][0], right_wing[0][1])
        for x, y in right_wing[1:]:
            wing.lineTo(side * x, y)
        wing.closeSubpath()
        p.drawPath(wing)
    p.drawEllipse(QRectF(-21, -44, 42, 72))  # body
    p.drawEllipse(QPointF(0, -56), 13, 13)  # head
    tail = QPainterPath()
    tail.moveTo(-14, 20)
    tail.lineTo(14, 20)
    for x, y in ((24, 58), (11, 50), (0, 62), (-11, 50), (-24, 58)):
        tail.lineTo(x, y)
    tail.closeSubpath()
    p.drawPath(tail)
    beak = QPainterPath()
    beak.moveTo(-10, -60)
    beak.lineTo(-27, -54)
    beak.lineTo(-10, -49)
    beak.closeSubpath()
    p.setBrush(QColor(GOLD))
    p.drawPath(beak)
    p.setBrush(QColor(RED))
    p.drawEllipse(QPointF(-4, -59), 3, 3)  # eye
    p.restore()


def _draw_fax(p: QPainter, c: QPointF, r: float) -> None:
    """A small fax machine in front of the eagle: body, paper coming out, display and keys."""
    p.save()
    p.translate(c)
    p.scale(r / 100, r / 100)
    p.setPen(QPen(QColor("#1f2328"), 2.5))
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(-24, -46, 48, 40))  # paper
    for i in range(3):
        p.drawLine(QPointF(-16, -36 + i * 9), QPointF(16, -36 + i * 9))
    p.setBrush(QColor("#4b5563"))
    p.drawRoundedRect(QRectF(-44, -12, 88, 50), 8, 8)  # body
    p.setBrush(QColor("#9ca3af"))
    p.drawRect(QRectF(-34, -4, 34, 14))  # display
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#e5e7eb"))
    for row in range(3):
        for col in range(3):
            p.drawEllipse(QPointF(10 + col * 11, 2 + row * 10), 3.2, 3.2)
    p.setBrush(QColor(RED))
    p.drawEllipse(QPointF(-28, 26), 4, 4)  # power light
    p.restore()
