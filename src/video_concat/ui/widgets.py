"""Widgets de la mesa de montaje: reproductor, fila de grupo y lista arrastrable."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_concat.core.frames import EdgeFrames
from video_concat.core.grouping import ClipGroup, GroupedClip
from video_concat.core.probe import MediaInfo
from video_concat.ui.formatting import format_duration, format_size
from video_concat.ui.styles import COLORS

ROW_MIME = "application/x-video-concat-row"
EMPTY_SURFACE = "#EDF2F5"


def frame_pixmap(frames: EdgeFrames | None) -> QPixmap:
    """Convierte el frame de preview en pixmap listo para pintar."""
    if frames is None:
        return QPixmap()
    image = QImage(
        frames.preview_rgb,
        frames.preview_size,
        frames.preview_size,
        3 * frames.preview_size,
        QImage.Format.Format_RGB888,
    )
    return QPixmap.fromImage(image.copy())


class FramePreview(QWidget):
    """Miniatura del primer fotograma, con hueco visible si no hay análisis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.setFixedSize(96, 84)

    def set_frames(self, frames: EdgeFrames | None) -> None:
        self._pixmap = frame_pixmap(frames)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.setPen(QPen(QColor(COLORS["line"]), 1))
        painter.setBrush(QColor(EMPTY_SURFACE))
        painter.drawRoundedRect(rect, 5, 5)

        if self._pixmap.isNull():
            painter.setPen(QColor(COLORS["ink_muted"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "sin\nfotograma")
            return

        scaled = self._pixmap.scaled(
            int(rect.width()),
            int(rect.height()),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = rect.x() + (rect.width() - scaled.width()) / 2
        y = rect.y() + (rect.height() - scaled.height()) / 2
        painter.drawPixmap(int(x), int(y), scaled)


class VideoPlayer(QFrame):
    """Hueco de reproducción de la fila: póster con play hasta que se pulsa."""

    play_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerSurface")
        self.setFixedSize(150, 84)
        self._media: MediaInfo | None = None
        self._video: QWidget | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    @property
    def media(self) -> MediaInfo | None:
        return self._media

    def set_clip(self, media: MediaInfo, frames: EdgeFrames | None) -> None:
        self.detach_video()
        self._media = media
        self.update()

    def attach_video(self, video: QWidget) -> None:
        """Inserta el QVideoWidget compartido en esta fila."""
        self.detach_video()
        self._video = video
        video.setParent(self)
        self._layout.addWidget(video)
        video.show()

    def detach_video(self) -> None:
        if self._video is not None:
            self._layout.removeWidget(self._video)
            self._video.setParent(None)
            self._video.hide()
            self._video = None

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(COLORS["line"]), 1))
        painter.setBrush(QColor(COLORS["ink"]))
        painter.drawRoundedRect(rect, 5, 5)

        if self._video is not None and self._video.isVisible():
            return

        # Sin vídeo activo: superficie de control, no un segundo póster (la
        # miniatura de la izquierda ya muestra el primer fotograma).
        center = rect.center()
        radius = 16
        button = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        painter.setBrush(QColor(COLORS["surface_raised"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(button)

        painter.setBrush(QColor(COLORS["ink"]))
        painter.drawPolygon(
            QPolygonF(
                [
                    center + QPointF(-4, -7),
                    center + QPointF(8, 0),
                    center + QPointF(-4, 7),
                ]
            )
        )

        if self._media is not None:
            painter.setPen(QColor(COLORS["surface"]))
            painter.drawText(
                QRectF(rect.x(), rect.bottom() - 18, rect.width(), 14),
                Qt.AlignmentFlag.AlignCenter,
                format_duration(self._media.duration),
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._media is not None:
            self.play_requested.emit(self._media)
            event.accept()
            return
        super().mousePressEvent(event)


class ClipGroupRow(QFrame):
    """Una fila = un grupo de clips que comparten primer y último fotograma."""

    selected = Signal(int)

    def __init__(self, group: ClipGroup, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ClipRow")
        self.group = group
        self.index = index
        self._active = 0
        self._selected = False
        self._drag_origin: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 12, 6)
        layout.setSpacing(10)

        self.handle = QLabel("⠿")
        self.handle.setObjectName("RowHandle")
        self.handle.setFixedWidth(16)
        self.handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.handle)

        self.preview = FramePreview()
        layout.addWidget(self.preview)

        self.player = VideoPlayer()
        layout.addWidget(self.player)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("RowTitle")
        self.detail = QLabel()
        self.detail.setObjectName("RowDetail")
        self.detail.setWordWrap(True)
        self.tags = QLabel()
        self.tags.setObjectName("RowTags")
        info.addWidget(self.title)
        info.addWidget(self.detail)
        info.addWidget(self.tags)
        info.addStretch(1)
        layout.addLayout(info, stretch=1)

        self.btn_members = QPushButton()
        self.btn_members.setObjectName("Variant")
        self.btn_members.clicked.connect(self._cycle_member)
        layout.addWidget(self.btn_members, alignment=Qt.AlignmentFlag.AlignTop)

        self._refresh()

    # --- estado ---
    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_active_member(self, index: int) -> None:
        self._active = index if 0 <= index < len(self.group.members) else 0
        self._refresh()

    @property
    def control(self) -> QFrame:
        return self.player

    @property
    def active(self) -> GroupedClip:
        return self.group.resolve(self._active)

    @property
    def active_index(self) -> int:
        return self._active

    def is_selected(self) -> bool:
        return self._selected

    def _cycle_member(self) -> None:
        if len(self.group.members) < 2:
            return
        self._active = (self._active + 1) % len(self.group.members)
        self._refresh()
        self.selected.emit(self.index)

    def _refresh(self) -> None:
        clip = self.active
        frames = clip.frames
        self.preview.set_frames(frames)
        self.player.set_clip(clip.media, frames)

        total = len(self.group.members)
        self.title.setText(f"{self._active + 1}/{total}  ·  {clip.media.label}")
        media = clip.media
        self.detail.setText(
            f"{media.resolution} · {media.fps_label} fps · {format_duration(media.duration)} · "
            f"{format_size(media.size_bytes)}"
        )
        if clip.problem:
            self.tags.setText(clip.problem)
            self.tags.setStyleSheet(f"color: {COLORS['warn']};")
        elif total > 1:
            self.tags.setText(f"grupo de {total} · {self.group.origin}")
            self.tags.setStyleSheet(f"color: {COLORS['ok']};")
        else:
            self.tags.setText("fila única")
            self.tags.setStyleSheet(f"color: {COLORS['ink_muted']};")

        self.btn_members.setText(f"variante {self._active + 1}/{total}" if total > 1 else "único")
        self.btn_members.setEnabled(total > 1)
        self.btn_members.setVisible(total > 1)

    # --- interacción ---
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self.selected.emit(self.index)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = (event.position().toPoint() - self._drag_origin).manhattanLength()
        if delta < 8:
            return
        mime = QMimeData()
        mime.setData(ROW_MIME, str(self.index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_origin = None

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        super().mouseReleaseEvent(event)


class InlinePlayback:
    """
    Reproductor único compartido por todas las filas.

    Se re-parenta al `VideoPlayer` de la fila activa y devuelve el control al
    pulsar play en otra fila. Ventaja frente a un player por fila: un solo
    decodificador, sin audio solapado y sin N hilos de vídeo en memoria.
    """

    def __init__(self, parent: QWidget) -> None:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._parent = parent
        self.video = QVideoWidget(parent)
        self.video.setMinimumSize(120, 60)
        self.video.hide()
        self.player = QMediaPlayer(parent)
        self.audio = QAudioOutput(parent)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.video.videoSink())
        self.current: MediaInfo | None = None
        self._slot: VideoPlayer | None = None
        self.player.errorOccurred.connect(self._on_error)

    def is_playing(self, media: MediaInfo) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer

        return (
            self.current is not None
            and self.current.path == media.path
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )

    def toggle(self, media: MediaInfo, slot: VideoPlayer) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self.current is not None and self.current.path == media.path:
            if playing:
                self.player.pause()
            else:
                self.player.play()
                self._attach(slot)
            return

        self.stop()
        self._attach(slot)
        self.current = media
        self.player.setSource(QUrl.fromLocalFile(str(media.path)))
        self.player.play()

    def _attach(self, slot: VideoPlayer) -> None:
        if self._slot is not slot:
            self._slot = slot
            slot.attach_video(self.video)

    def stop(self) -> None:
        self.player.stop()
        self.current = None
        if self._slot is not None:
            self._slot.detach_video()
            self._slot.update()
            self._slot = None

    def _on_error(self, _error, message: str) -> None:
        from video_concat.logutil import get_logger

        get_logger("ui").warning("Error de reproducción: %s", message)


class GroupRowList(QWidget):
    """Contenedor vertical de filas con reordenación por arrastre."""

    rows_reordered = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ClipGroupRow] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self.setAcceptDrops(True)
        self._drag_row: ClipGroupRow | None = None
        self._drop_index: int | None = None

    def rows(self) -> list[ClipGroupRow]:
        return list(self._rows)

    def clear(self) -> None:
        for row in self._rows:
            self._layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

    def add_row(self, row: ClipGroupRow) -> None:
        self._rows.append(row)
        self._layout.insertWidget(self._layout.count() - 1, row)

    def _on_row_selected(self, index: int) -> None:
        for row in self._rows:
            row.set_selected(row.index == index)

    def reorder(self, source: int, target: int) -> None:
        if source == target or not (0 <= source < len(self._rows)):
            return
        row = self._rows.pop(source)
        destination = max(0, min(target, len(self._rows)))
        self._rows.insert(destination, row)
        for row_ in self._rows:
            self._layout.removeWidget(row_)
        for row_ in self._rows:
            self._layout.insertWidget(self._layout.count() - 1, row_)
        for i, row_ in enumerate(self._rows):
            row_.index = i
        self.rows_reordered.emit()

    # --- drag & drop de filas ---
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(ROW_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(ROW_MIME):
            return
        pos = event.position().toPoint()
        self._drop_index = self._index_at(pos.y())
        for row in self._rows:
            row.setProperty("dropTarget", "false")
            row.style().unpolish(row)
            row.style().polish(row)
        target = self._row_at(self._drop_index)
        if target is not None:
            target.setProperty("dropTarget", "true")
            target.style().unpolish(target)
            target.style().polish(target)
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(ROW_MIME):
            return
        raw = bytes(event.mimeData().data(ROW_MIME)).decode()
        try:
            source = int(raw)
        except ValueError:
            return
        target = self._index_at(event.position().toPoint().y())
        self._drop_index = None
        self.reorder(source, target)
        event.acceptProposedAction()

    def _index_at(self, y: int) -> int:
        for i, row in enumerate(self._rows):
            geometry = row.geometry()
            if geometry.isValid() and y < geometry.center().y():
                return i
        return len(self._rows)

    def _row_at(self, index: int | None) -> ClipGroupRow | None:
        if index is None or not (0 <= index < len(self._rows)):
            return None
        return self._rows[index]


class TimelineStrip(QWidget):
    """Franja proporcional a la duración de cada clip — firma visual del producto."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[MediaInfo] = []
        self.setMinimumHeight(38)
        self.setMaximumHeight(42)

    def set_items(self, items: list[MediaInfo]) -> None:
        self._items = list(items)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(8, 4, self.width() - 16, 18)
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
            QRectF(8, 24, self.width() - 16, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._caption(),
        )

    def _caption(self) -> str:
        if not self._items:
            return "Añade clips para ver la línea de tiempo"
        total = sum(m.duration for m in self._items)
        mins, secs = divmod(int(total), 60)
        hours, mins = divmod(mins, 60)
        stamp = f"{hours:02d}:{mins:02d}:{secs:02d}" if hours else f"{mins:02d}:{secs:02d}"
        return f"{len(self._items)} clips · duración estimada {stamp}"




