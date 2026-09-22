"""Widgets auxiliares de la UI."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from video_concat.core.probe import MediaInfo
from video_concat.ui.styles import COLORS


class TimelineStrip(QWidget):
    """Franja proporcional a la duración de cada clip — firma visual del producto."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[MediaInfo] = []
        self.setMinimumHeight(52)
        self.setMaximumHeight(64)

    def set_items(self, items: list[MediaInfo]) -> None:
        self._items = list(items)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(8, 14, self.width() - 16, 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["timeline_track"]))
        painter.drawRoundedRect(track, 4, 4)

        total = sum(max(m.duration, 0.01) for m in self._items) or 1.0
        palette = [COLORS["clip_a"], COLORS["clip_b"], COLORS["clip_c"], COLORS["accent"]]
        x = track.x()
        for i, item in enumerate(self._items):
            w = track.width() * (max(item.duration, 0.01) / total)
            rect = QRectF(x + 1, track.y() + 1, max(w - 2, 2), track.height() - 2)
            painter.setBrush(QColor(palette[i % len(palette)]))
            painter.drawRoundedRect(rect, 3, 3)
            x += w

        painter.setPen(QPen(QColor(COLORS["ink_muted"]), 1))
        painter.drawText(
            QRectF(8, 40, self.width() - 16, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._caption(),
        )

    def _caption(self) -> str:
        if not self._items:
            return "Añade clips para ver la línea de tiempo"
        total = sum(m.duration for m in self._items)
        mins, secs = divmod(int(total), 60)
        hours, mins = divmod(mins, 60)
        if hours:
            stamp = f"{hours:02d}:{mins:02d}:{secs:02d}"
        else:
            stamp = f"{mins:02d}:{secs:02d}"
        return f"{len(self._items)} clips · duración estimada {stamp}"
