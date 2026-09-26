"""VocaVault desktop application entry point."""

from __future__ import annotations

import sys
from argparse import ArgumentParser
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from .library import LibraryService
from .paths import default_database_path
from .ui import MainWindow, apply_application_style


def main(argv: Sequence[str] | None = None) -> int:
    """Create the local library service and start the Qt event loop."""
    arguments = list(sys.argv if argv is None else argv)
    parser = ArgumentParser(description="VocaVault local project library")
    parser.add_argument("--database", type=Path, help="path to the library database")
    options, qt_arguments = parser.parse_known_args(arguments[1:])
    app = QApplication([arguments[0], *qt_arguments])
    apply_application_style(app)

    database_path = (options.database or default_database_path()).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    writer_lock = QLockFile(f"{database_path}.lock")
    writer_lock.setStaleLockTime(0)
    if not writer_lock.tryLock(0):
        QMessageBox.warning(
            None,
            "VocaVault is already running",
            "Another VocaVault process is using this library.",
        )
        return 1
    try:
        library = LibraryService(database_path)
    except Exception as exc:  # noqa: BLE001 - startup errors must remain user-visible.
        QMessageBox.critical(
            None,
            "VocaVault could not start",
            f"The local library could not be opened.\n\n{exc}",
        )
        writer_lock.unlock()
        return 1

    window = MainWindow(library)
    window.show()
    exit_code = app.exec()
    window.wait_for_workers()
    writer_lock.unlock()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
