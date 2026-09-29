"""Small native widgets that remain usable in a narrow inspector."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QSizePolicy


class WrappingLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


class PathLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)

    def setPath(self, path):
        self.path = path
        self.setToolTip(path)
        self._update_text()

    def resizeEvent(self, event):
        self._update_text()
        super().resizeEvent(event)

    def _update_text(self):
        self.setText(
            self.fontMetrics().elidedText(
                self.path, Qt.TextElideMode.ElideMiddle, self.width()
            )
        )


class MultilineEdit(QPlainTextEdit):
    """Wrapping editor with QLineEdit-compatible accessors for small forms."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setMinimumHeight(54)
        self.setMaximumHeight(86)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def text(self):
        return self.toPlainText()

    def setText(self, text):
        self.setPlainText(text)
