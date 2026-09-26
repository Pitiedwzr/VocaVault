from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from vocavault.library import LibraryService
from vocavault.ui import MainWindow


class _FakeLibrary:
    def list_projects(self, **_filters: object) -> list[object]:
        return []

    def close(self) -> None:
        pass


class _CancellableLibrary(_FakeLibrary):
    def __init__(self) -> None:
        self.started = Event()
        self.cancellation_seen = Event()

    def import_paths(
        self,
        paths: object,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[int, int, str], None],
    ) -> list[object]:
        del paths
        progress(0, 1, "sample.svp")
        self.started.set()
        while not cancelled():
            self.cancellation_seen.wait(timeout=0.01)
        self.cancellation_seen.set()
        return []


def test_main_window_can_be_constructed(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(LibraryService(tmp_path / "ui.sqlite3"))

    assert window.windowTitle() == "VocaVault"
    assert window.project_table.columnCount() == len(window.TABLE_COLUMNS)

    window.thread_pool.waitForDone()
    app.processEvents()
    window.close()
    app.processEvents()


def test_worker_result_callback_runs_on_gui_thread() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(_FakeLibrary())
    window.thread_pool.waitForDone()
    app.processEvents()
    observed: list[tuple[QThread, QThread]] = []

    window._start_worker(
        lambda _context: QThread.currentThread(),
        lambda worker_thread: observed.append((worker_thread, QThread.currentThread())),
        "Checking worker delivery…",
    )
    window.thread_pool.waitForDone()
    app.processEvents()

    assert observed
    assert observed[0][0] != app.thread()
    assert observed[0][1] == app.thread()
    window.close()


def test_close_waits_for_active_worker() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(_FakeLibrary())
    window.thread_pool.waitForDone()
    app.processEvents()
    started = Event()
    release = Event()

    def work(_context: object) -> None:
        started.set()
        release.wait(timeout=5)

    window._start_worker(work, lambda _result: None, "Working…", mutating=True)
    assert started.wait(timeout=2)

    assert window.close() is False
    assert window._closing_when_idle is True
    release.set()
    window.thread_pool.waitForDone()
    app.processEvents()
    app.processEvents()

    assert window._active_jobs == 0


def test_import_can_be_cancelled_between_files() -> None:
    app = QApplication.instance() or QApplication([])
    library = _CancellableLibrary()
    window = MainWindow(library)
    window.thread_pool.waitForDone()
    app.processEvents()

    window._import_paths(["sample.svp"])
    assert library.started.wait(timeout=2)
    window.cancel_current_operation()
    window.thread_pool.waitForDone()
    app.processEvents()
    window.thread_pool.waitForDone()
    app.processEvents()

    assert library.cancellation_seen.is_set()
    assert window._active_jobs == 0
    assert window.import_files_action.isEnabled()
    window.close()
