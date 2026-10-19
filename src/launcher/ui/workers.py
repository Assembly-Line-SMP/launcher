"""Generic background-thread worker.

Every network call, download, or subprocess run in this app is blocking
Python code living in launcher.core / launcher.auth. None of it may run on
the Qt GUI thread or the window will freeze and the OS will flag it as
unresponsive. This module provides one reusable QThread wrapper so each
call site doesn't reinvent thread plumbing.
"""

from __future__ import annotations
from threading import Thread

import logging
import traceback
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

logger = logging.getLogger(__name__)

class Signal:
    def __init__(self):
        self._subscribers = []

    def connect(self, callback: Callable[..., Any]) -> None:
        self._subscribers.append(callback)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        # Subscribers touch GTK widgets, so always run them on the main loop.
        for subscriber in list(self._subscribers):
            GLib.idle_add(self._invoke, subscriber, args, kwargs)

    @staticmethod
    def _invoke(subscriber: Callable[..., Any], args: tuple, kwargs: dict) -> bool:
        try:
            subscriber(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.exception("Signal subscriber failed")
        return GLib.SOURCE_REMOVE


class WorkerSignals:
    def __init__(self) -> None:
        self.finished = Signal()  # result
        self.error = Signal()  # message, traceback_str
        self.progress = Signal()  # label, done, total


class Worker(Thread):
    """Runs ``fn(*args, progress_callback=..., **kwargs)`` on a background
    thread. ``fn`` does not need to accept progress_callback if
    ``wants_progress=False``.

    Usage:
        worker = Worker(sync_instance, version, progress=True)
        worker.signals.finished.connect(on_done)
        worker.signals.error.connect(on_error)
        worker.signals.progress.connect(on_progress)
        worker.start()

    Caller must keep a reference to ``worker`` alive until it finishes
    (e.g. as a field on the widget that started it) -- PySide6 will
    silently drop signals from a QThread that's been garbage collected
    mid-run.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        *args: Any,
        wants_progress: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._wants_progress = wants_progress

    def run(self) -> None:
        print("Worker started")
        try:
            if self._wants_progress:
                self._kwargs.setdefault(
                    "progress",
                    lambda label, done, total: self.signals.progress.emit(
                        label, done, total
                    ),
                )
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - must not crash the GUI thread
            logger.exception("Background task failed")
            self.signals.error.emit(str(exc), traceback.format_exc())
        else:
            self.signals.finished.emit(result)
    def requestInterruption(self) -> None:
        """Requests that the worker thread stop execution."""
        self._interrupted = True

    def isRunning(self) -> bool:
        """Checks if the worker thread is still running."""
        return self.is_alive()
    def wait(self, timeout: float|None = None) -> None:
        """Waits for the worker thread to finish execution."""
        self.join(timeout)