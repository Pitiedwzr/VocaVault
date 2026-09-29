from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_ui_settings(tmp_path: Path, monkeypatch):
    """Tests must not modify the user's window/layout preferences."""
    from PySide6.QtCore import QSettings

    from vocavault import ui

    monkeypatch.setattr(
        ui,
        "QSettings",
        lambda *_: QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat),
    )
