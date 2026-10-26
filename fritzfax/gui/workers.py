"""Run blocking core calls off the GUI thread and deliver results via queued signals."""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..core import job


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _Signals):
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - every error goes to the GUI
            exc.__traceback_text__ = traceback.format_exc()  # type: ignore[attr-defined]
            self.signals.failed.emit(exc)
        else:
            self.signals.done.emit(result)


_keep: set[_Signals] = set()


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None) -> _Signals:
    """Run fn in the global thread pool; callbacks run in the GUI thread."""
    signals = _Signals()
    _keep.add(signals)

    def finish(_=None):
        _keep.discard(signals)

    if on_done:
        signals.done.connect(on_done)
    if on_error:
        signals.failed.connect(on_error)
    signals.done.connect(finish)
    signals.failed.connect(finish)
    QThreadPool.globalInstance().start(_Task(fn, signals))
    return signals


class SendEvents(QObject, job.Events):
    """job.Events that forwards everything to the GUI thread as signals."""

    phase = Signal(str, str, str)
    status = Signal(str, object)
    two_factor = Signal(str, object)

    def __init__(self):
        QObject.__init__(self)
        self.cancel_event = threading.Event()

    def on_phase(self, dest, phase, detail=""):
        self.phase.emit(dest, phase, detail)

    def on_status(self, dest, status):
        self.status.emit(dest, status)

    def on_two_factor(self, dest, challenge):
        self.two_factor.emit(dest, challenge)

    def should_cancel(self):
        return self.cancel_event.is_set()


def wait_all(timeout_ms: int = 30000) -> bool:
    return QThreadPool.globalInstance().waitForDone(timeout_ms)
