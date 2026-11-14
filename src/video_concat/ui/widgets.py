"""Widgets de la mesa de montaje: miniatura de fotogramas, fila de grupo y lista."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from video_concat.core.frames import EdgeFrames
from video_concat.core.grouping import ClipGroup, GroupedClip
from video_concat.core.probe import MediaInfo
from video_concat.ui.formatting import format_duration, format_size
from video_concat.ui.layout_metrics import (
    THUMB_MAX,
    TILE_CAP,
    TILE_GAP,
    TILE_HEAD_SPACING,
    TILE_PADDING,
    TILE_PLAY_SIZE,
    tile_metrics,
)
from video_concat.ui.styles import COLORS

ROW_MIME = "application/x-video-concat-row"
EMPTY_SURFACE = "#EDF2F5"
# Hueco reservado bajo las tarjetas para la barra de scroll horizontal.
SCROLLBAR_RESERVE = 18
# Interruptor «mostrar siguientes» del pie de la tarjeta.
TOGGLE_WIDTH = 28
TOGGLE_HEIGHT = 15


class ElidedLabel(QLabel):
    """Etiqueta de una línea que recorta con «…» en vez de desbordar la tarjeta."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._apply()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._apply()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        width = max(self.width(), 24)
        elided = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, width
        )
        super().setText(elided)


def frame_pixmap(frames: EdgeFrames | None, last: bool = False) -> QPixmap:
    """Convierte el RGB de un fotograma extremo en pixmap listo para pintar."""
    if frames is None:
        return QPixmap()
    raw = frames.last_rgb if last else frames.first_rgb
    size = frames.preview_size
    image = QImage(raw, size, size, 3 * size, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


class FrameThumb(QWidget):
    """Miniatura de un fotograma extremo, etiquetada como primero o último."""

    def __init__(self, caption: str, side: int = THUMB_MAX, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._pixmap = QPixmap()
        self.set_fixed_side(side)

    def set_fixed_side(self, side: int) -> None:
        self.setFixedSize(side, side)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self.update()

    @property
    def has_frame(self) -> bool:
        return not self._pixmap.isNull()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(COLORS["line"]), 1))
        painter.setBrush(QColor(EMPTY_SURFACE))
        painter.drawRoundedRect(rect, 4, 4)

        if self._pixmap.isNull():
            painter.setPen(QColor(COLORS["ink_muted"]))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "sin\nfotograma")
        else:
            scaled = self._pixmap.scaled(
                int(rect.width()),
                int(rect.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = rect.x() + (rect.width() - scaled.width()) / 2
            y = rect.y() + (rect.height() - scaled.height()) / 2
            painter.drawPixmap(int(x), int(y), scaled)

        if self.width() < 72:
            return  # sin sitio para la etiqueta: el tooltip de la tarjeta ya lo explica
        badge = QRectF(rect.x() + 3, rect.y() + 3, 42, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["ink"]))
        painter.setOpacity(0.78)
        painter.drawRoundedRect(badge, 3, 3)
        painter.setOpacity(1.0)
        painter.setPen(QColor(COLORS["surface_raised"]))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, self._caption)


class ToggleSwitch(QAbstractButton):
    """Interruptor compacto y checkable, sin texto; hereda `toggled(bool)`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(TOGGLE_WIDTH, TOGGLE_HEIGHT)
        self.setAccessibleName("Mostrar siguientes")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TOGGLE_WIDTH, TOGGLE_HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        on = self.isChecked() and enabled
        track = QRectF(1, 1, self.width() - 2, self.height() - 2)
        edge = COLORS["accent"] if on else COLORS["line"]
        fill = COLORS["accent"] if on else COLORS["surface_raised"]
        if not enabled:
            edge, fill = "#CBD5DC", "#EDF1F4"
        painter.setPen(QPen(QColor(edge), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        knob = track.height() - 4
        x = track.right() - knob - 2 if self.isChecked() else track.left() + 2
        knob_color = COLORS["surface_raised"] if on else COLORS["ink_muted"]
        if not enabled:
            knob_color = "#C3CDD4"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(knob_color))
        painter.drawEllipse(QRectF(x, track.top() + 2, knob, knob))


class ClipTile(QFrame):
    """Un clip dentro de la fila: primer y último fotograma, nombre y play."""

    activated = Signal(object)
    play_requested = Signal(object)
    follow_toggled = Signal(object, bool)

    def __init__(self, clip: GroupedClip, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ClipTile")
        self.clip = clip
        self._active = active
        self._width = TILE_CAP
        self._thumb_side = THUMB_MAX
        self.setFixedWidth(self._width)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(TILE_PADDING, 6, TILE_PADDING, 6)
        layout.setSpacing(3)

        head = QHBoxLayout()
        head.setSpacing(TILE_HEAD_SPACING)
        self.thumb_first = FrameThumb("primero")
        self.thumb_last = FrameThumb("último")
        head.addWidget(self.thumb_first)
        head.addWidget(self.thumb_last)
        head.addStretch(1)
        self.btn_play = QPushButton("▶")
        self.btn_play.setObjectName("TilePlay")
        self.btn_play.setFixedSize(TILE_PLAY_SIZE, TILE_PLAY_SIZE)
        self.btn_play.setToolTip("Reproducir en el reproductor del sistema")
        self.btn_play.clicked.connect(lambda: self.play_requested.emit(self.clip.media))
        head.addWidget(self.btn_play, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        media = clip.media
        self.name = ElidedLabel(media.label)
        self.name.setObjectName("TileName")
        self.name.setToolTip(f"{media.path}")
        layout.addWidget(self.name)

        self.detail = ElidedLabel(
            f"{media.resolution} · {media.fps_label} fps · {format_duration(media.duration)}"
        )
        self.detail.setObjectName("TileDetail")
        self.detail.setToolTip(self.detail.full_text())
        layout.addWidget(self.detail)

        self.status = ElidedLabel()
        self.status.setObjectName("TileStatus")
        if clip.problem:
            self.status.setText(clip.problem)
            self.status.setStyleSheet(f"color: {COLORS['warn']};")
            self.status.setToolTip(clip.problem)
        else:
            self.status.setText(format_size(media.size_bytes))
            self.status.setStyleSheet(f"color: {COLORS['ink_muted']};")

        footer = QHBoxLayout()
        footer.setSpacing(4)
        footer.addWidget(self.status, stretch=1)
        self.follow_switch = ToggleSwitch()
        self.follow_switch.setObjectName("FollowSwitch")
        self.follow_switch.setToolTip(
            "Mostrar solo los clips que empiezan como termina este"
        )
        self.follow_switch.toggled.connect(
            lambda checked: self.follow_toggled.emit(self.clip.media.path, checked)
        )
        footer.addWidget(self.follow_switch, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(footer)

        layout.addStretch(1)
        self.set_frames(clip.frames)
        self.set_active(active)

    def set_tile_metrics(self, width: int, thumb_side: int) -> None:
        """Ajusta el ancho de la tarjeta y el lado de sus dos miniaturas."""
        if width == self._width and thumb_side == self._thumb_side:
            return
        self._width = width
        self._thumb_side = thumb_side
        self.thumb_first.set_fixed_side(thumb_side)
        self.thumb_last.set_fixed_side(thumb_side)
        self.setFixedWidth(width)
        self.updateGeometry()

    @property
    def tile_width(self) -> int:
        return self._width

    def height_hint(self) -> int:
        return self.sizeHint().height()

    def set_frames(self, frames: EdgeFrames | None) -> None:
        self.thumb_first.set_pixmap(frame_pixmap(frames, last=False))
        self.thumb_last.set_pixmap(frame_pixmap(frames, last=True))

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    @property
    def is_active(self) -> bool:
        return self._active

    def set_follow_checked(self, checked: bool) -> None:
        """Sincroniza el interruptor sin reemitir `follow_toggled`."""
        switch = self.follow_switch
        switch.blockSignals(True)
        switch.setChecked(checked)
        switch.blockSignals(False)

    def set_follow_available(self) -> None:
        """Sin firmas de fotogramas no hay nada que comparar: el interruptor se apaga."""
        usable = self.clip.frames is not None
        self.follow_switch.setEnabled(usable)
        if not usable:
            self.follow_switch.setToolTip(
                "Sin fotogramas analizados: no se puede comparar con los demás clips"
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self)
            event.accept()
            return
        super().mousePressEvent(event)


class DragHandle(QLabel):
    """Asa de arrastre de la fila: inicia el reordenado sin pisar los clics del clip."""

    drag_started = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("⠿", parent)
        self.setObjectName("RowHandle")
        self.setFixedWidth(16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._origin: QPoint | None = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._origin).manhattanLength() < 8:
            return
        origin = self._origin
        self._origin = None
        self.drag_started.emit(origin)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._origin = None
        super().mouseReleaseEvent(event)


class ClipGroupRow(QFrame):
    """Una fila = un grupo; todos sus clips van en la misma línea."""

    selected = Signal(int)
    play_requested = Signal(object)
    follow_toggled = Signal(object, bool)

    def __init__(self, group: ClipGroup, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ClipRow")
        self.group = group
        self.index = index
        self._active = 0
        self._selected = False
        self._tiles: list[ClipTile] = []
        self._metrics: tuple[int, int] | None = None
        self._relayouting = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 12, 6)
        layout.setSpacing(10)

        self.handle = DragHandle()
        self.handle.drag_started.connect(self.start_drag)
        layout.addWidget(self.handle, alignment=Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)
        column.setContentsMargins(0, 2, 0, 0)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("RowTitle")
        self.tags = QLabel()
        self.tags.setObjectName("RowTags")
        head.addWidget(self.title)
        head.addWidget(self.tags)
        head.addStretch(1)
        column.addLayout(head)

        self.strip = QWidget()
        self._strip_layout = QHBoxLayout(self.strip)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._strip_layout.setSpacing(TILE_GAP)
        self._strip_layout.addStretch(1)

        self.scroller = QScrollArea()
        self.scroller.setObjectName("TileScroller")
        self.scroller.setWidget(self.strip)
        self.scroller.setWidgetResizable(True)
        self.scroller.setFrameShape(QFrame.Shape.NoFrame)
        self.scroller.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroller.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scroller.viewport().installEventFilter(self)
        column.addWidget(self.scroller)

        layout.addLayout(column, stretch=1)

        for member in group.members:
            tile = ClipTile(member, active=False)
            tile.activated.connect(self._on_tile_activated)
            tile.play_requested.connect(self.play_requested)
            tile.follow_toggled.connect(self.follow_toggled)
            tile.set_follow_available()
            self._strip_layout.insertWidget(self._strip_layout.count() - 1, tile)
            self._tiles.append(tile)

        self._refresh()
        self.relayout_tiles()

    # --- estado ---
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout_tiles()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """El viewport del scroller conoce su ancho definitivo tras el primer layout."""
        if watched is self.scroller.viewport() and event.type() == QEvent.Type.Resize:
            self.relayout_tiles()
        return super().eventFilter(watched, event)

    def relayout_tiles(self, force: bool = False) -> None:
        """
        Reparte el ancho del grupo entre sus clips para que quepan en una línea.

        Las miniaturas se encogen hasta `THUMB_MIN`; si el grupo no cabe ni así,
        se queda con el tamaño mínimo y el scroller permite desplazarse. Solo
        cuentan las tarjetas visibles: el filtro «mostrar siguientes» libera su
        hueco en vez de dejar agujeros.
        """
        if not self._tiles or self._relayouting:
            return
        visible = self.visible_tiles
        if not visible:
            return
        available = self.scroller.viewport().width()
        if available <= 0:
            available = self.width() - 70
        metrics = tile_metrics(len(visible), max(available, 1))
        if metrics == self._metrics and not force:
            return

        self._relayouting = True
        try:
            self._metrics = metrics
            width, side = metrics
            for tile in visible:
                tile.set_tile_metrics(width, side)
            self._sync_scroller_height()
        finally:
            self._relayouting = False

    def set_index(self, index: int) -> None:
        self.index = index
        self._refresh()

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
    def active(self) -> GroupedClip:
        return self.group.resolve(self._active)

    @property
    def active_index(self) -> int:
        return self._active

    @property
    def tiles(self) -> list[ClipTile]:
        return list(self._tiles)

    @property
    def visible_tiles(self) -> list[ClipTile]:
        return [tile for tile in self._tiles if not tile.isHidden()]

    def is_selected(self) -> bool:
        return self._selected

    def apply_follow_filter(self, kept: set[Path] | None) -> None:
        """
        Oculta las tarjetas fuera del filtro y recoloca las visibles.

        `kept=None` significa sin filtro: se muestran todas. Si la tarjeta activa
        queda oculta, el clip activo de la fila pasa a la primera visible: nunca
        se concatena algo que el filtro ha escondido.
        """
        for tile in self._tiles:
            tile.setVisible(kept is None or tile.clip.media.path in kept)

        if kept is not None and self._tiles[self._active].isHidden():
            first_visible = next(
                (i for i, tile in enumerate(self._tiles) if not tile.isHidden()), None
            )
            if first_visible is not None:
                self._active = first_visible
        self.relayout_tiles(force=True)
        self._refresh()

    def sync_follow_switches(self, anchor: Path | None) -> None:
        """Solo el ancla puede estar encendida: el filtro es único en toda la lista."""
        for tile in self._tiles:
            tile.set_follow_checked(anchor is not None and tile.clip.media.path == anchor)

    def _on_tile_activated(self, tile: ClipTile) -> None:
        self._active = self._tiles.index(tile)
        self._refresh()
        self.selected.emit(self.index)

    def _sync_scroller_height(self) -> None:
        """
        Alto del scroller = alto que pide una tarjeta + hueco para la barra.

        Se usa `sizeHint` (no la altura real): las tarjetas no deben estirarse en
        vertical, y leer su altura real realimentaría el cálculo del scroller.
        """
        visible = self.visible_tiles
        if not visible:
            return
        content = max(tile.height_hint() for tile in visible)
        self.scroller.setFixedHeight(content + SCROLLBAR_RESERVE)

    def _refresh(self) -> None:
        total = len(self.group.members)
        for i, tile in enumerate(self._tiles):
            tile.set_active(i == self._active)

        visible = len(self.visible_tiles)
        position = f"Fila {self.index + 1}"
        if total > 1:
            active = self.active.media.label
            shown = f"{visible} de {total} variantes" if visible < total else f"{total} variantes"
            self.title.setText(f"{position} · {shown}, activa {active}")
            self.tags.setText(self.group.origin)
            self.tags.setStyleSheet(f"color: {COLORS['ok']};")
        else:
            self.title.setText(f"{position} · {self.active.media.label}")
            self.tags.setText(self.group.origin)
            color = COLORS["warn"] if self.active.problem else COLORS["ink_muted"]
            self.tags.setStyleSheet(f"color: {color};")

        self.handle.setToolTip("Arrastra para reordenar la fila")

    # --- arrastre ---
    def start_drag(self, _origin: QPoint | None = None) -> None:
        """Arranca el arrastre de la fila; solo lo inicia el asa."""
        mime = QMimeData()
        mime.setData(ROW_MIME, str(self.index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.exec(Qt.DropAction.MoveAction)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.index)
        super().mousePressEvent(event)


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

    def reorder(self, source: int, target: int) -> None:
        """
        Mueve la fila `source` a la posición `target`.

        `target` es un índice de inserción calculado sobre la lista previa al
        `pop`, así que al mover hacia abajo hay que descontar la propia fila:
        sin ese ajuste el arrastre se salta un hueco.
        """
        if source == target or not (0 <= source < len(self._rows)):
            return
        row = self._rows.pop(source)
        if target > source:
            target -= 1
        destination = max(0, min(target, len(self._rows)))
        self._rows.insert(destination, row)
        for row_ in self._rows:
            self._layout.removeWidget(row_)
        for row_ in self._rows:
            self._layout.insertWidget(self._layout.count() - 1, row_)
        for i, row_ in enumerate(self._rows):
            row_.set_index(i)
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
        """Índice (en la lista completa) donde insertar; las filas ocultas no cuentan."""
        for i, row in enumerate(self._rows):
            if row.isHidden():
                continue
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
