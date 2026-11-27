"""Widgets de la sala de montaje: tarjetas de clip, filas y timeline."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from video_concat.core.frames import EdgeFrames
from video_concat.core.grouping import ClipGroup, GroupedClip
from video_concat.core.probe import MediaInfo
from video_concat.ui.formatting import format_duration, format_size, format_timecode
from video_concat.ui.layout_metrics import (
    CARD_BASE_THUMB,
    CARD_COLUMN_GAP,
    CARD_PADDING,
    CARD_THUMB_GAP,
    MONTAGE_CAPTION_H,
    MONTAGE_EDGE,
    MONTAGE_HEIGHT,
    MONTAGE_PLAYHEAD_W,
    card_font_sizes,
    card_metrics,
    clamp_zoom,
    montage_widths,
)
from video_concat.ui.styles import COLORS
from video_concat.ui.timeline import TimelineMember, TimelineSlot

ROW_MIME = "application/x-video-concat-row"
TIMELINE_MIME = "application/x-video-concat-timeline"
THUMB_FILL = "#080D11"
# Hueco que ocupa el asa y los márgenes de una fila antes de las tarjetas.
ROW_SIDE_SPACE = 46
# Interruptor «mostrar siguientes» del pie de la tarjeta.
TOGGLE_WIDTH = 28
TOGGLE_HEIGHT = 15
TILE_ACTION_SIZE = 22
TILE_REMOVE_SIZE = 18
# Píxeles que hay que mover el ratón sobre un tramo para arrastrarlo.
DRAG_THRESHOLD = 6
# Margen antes de cerrar la bandeja de variantes al salir del timeline.
VARIANT_HIDE_MS = 180
VARIANT_CHIP_W = 104
VARIANT_THUMB_H = 56
VARIANT_CHIP_H = VARIANT_THUMB_H + 24


class ElidedLabel(QLabel):
    """Etiqueta de una línea que recorta con «…» en vez de desbordar la tarjeta."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._apply()

    def text(self) -> str:  # noqa: N802
        """Devuelve el texto completo, no el recortado que se pinta."""
        return self._full

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        """El recorte con «…» permite cualquier ancho: no se impone el texto entero."""
        return QSize(24, super().minimumSizeHint().height())

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


def cover_pixmap(frames: EdgeFrames | None, width: int, height: int) -> QPixmap | None:
    """
    Primer fotograma escalado «a sangre» para un recuadro dado.

    `KeepAspectRatioByExpanding` deja una imagen que cubre el recuadro; se
    recorta el centro para no deformar el fotograma. Cachear este pixmap en el
    layout evita reescalarlo en cada repintado del cursor.
    """
    if frames is None or width < 2 or height < 2:
        return None
    source = frame_pixmap(frames, last=False)
    if source.isNull():
        return None
    scaled = source.scaled(
        int(width),
        int(height),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - int(width)) // 2)
    y = max(0, (scaled.height() - int(height)) // 2)
    return scaled.copy(x, y, int(width), int(height))


class FrameThumb(QWidget):
    """Miniatura de un fotograma extremo, etiquetada como primero o último."""

    def __init__(
        self,
        caption: str,
        short: str = "",
        side: int = CARD_BASE_THUMB,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._caption = caption
        self._short = short or caption[:1]
        self._pixmap = QPixmap()
        self.setToolTip(caption.capitalize())
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
        painter.setBrush(QColor(THUMB_FILL))
        painter.drawRoundedRect(rect, 4, 4)

        if self._pixmap.isNull():
            painter.setPen(QColor(COLORS["ink_muted"]))
            message = "sin\nfotograma" if self.width() >= 72 else "—"
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)
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

        badge_w, badge_h = (42.0, 13.0) if self.width() >= 64 else (16.0, 12.0)
        badge = QRectF(rect.x() + 2, rect.y() + 2, badge_w, badge_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(4, 8, 10, 190))
        painter.drawRoundedRect(badge, 3, 3)
        font = QFont(self.font())
        font.setPixelSize(8 if self.width() < 64 else 9)
        painter.setFont(font)
        painter.setPen(QColor(COLORS["ink"]))
        label = self._short if self.width() < 64 else self._caption
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)


class ToggleSwitch(QAbstractButton):
    """Interruptor compacto y checkable, sin texto; hereda `toggled(bool)`."""

    def __init__(
        self, accessible_name: str = "Interruptor", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(TOGGLE_WIDTH, TOGGLE_HEIGHT)
        self.setAccessibleName(accessible_name)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TOGGLE_WIDTH, TOGGLE_HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        on = self.isChecked() and enabled
        track = QRectF(1, 1, self.width() - 2, self.height() - 2)
        if not enabled:
            edge, fill = COLORS["line_soft"], COLORS["surface"]
        elif on:
            edge, fill = COLORS["accent"], COLORS["accent"]
        else:
            edge, fill = COLORS["line"], COLORS["surface_raised"]
        painter.setPen(QPen(QColor(edge), 1))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        knob = track.height() - 4
        x = track.right() - knob - 2 if self.isChecked() else track.left() + 2
        knob_color = "#14100C" if on else COLORS["ink_muted"]
        if not enabled:
            knob_color = "#4B5A63"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(knob_color))
        painter.drawEllipse(QRectF(x, track.top() + 2, knob, knob))


class IconButton(QAbstractButton):
    """Botón de icono pintado a mano: sin ficheros ni fuentes de iconos."""

    def __init__(
        self,
        icon: str,
        size: int = 30,
        tooltip: str = "",
        parent: QWidget | None = None,
        *,
        checkable: bool = False,
        filled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._icon = icon
        self._hovered = False
        self._filled = filled
        self.setCheckable(checkable)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_icon(self, icon: str) -> None:
        if icon == self._icon:
            return
        self._icon = icon
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        checked = self.isChecked()
        hovered = self._hovered and enabled
        rect = QRectF(0, 0, self.width(), self.height())

        if self._filled:
            fill = COLORS["accent_hi"] if hovered and enabled else COLORS["accent"]
            if not enabled:
                fill = COLORS["line"]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
            color = QColor("#14100C" if enabled else COLORS["bg"])
        else:
            if checked:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(COLORS["accent_soft"]))
                painter.drawRoundedRect(rect, 6, 6)
            elif hovered:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(COLORS["surface_hi"]))
                painter.drawRoundedRect(rect, 6, 6)
            if not enabled:
                color = QColor("#4A5962")
            elif checked:
                color = QColor(COLORS["accent"])
            else:
                color = QColor(COLORS["ink"] if hovered else COLORS["ink_muted"])

        self._paint_glyph(painter, color)

    def _paint_glyph(self, painter: QPainter, color: QColor) -> None:
        painter.save()
        pad = self.width() * 0.27
        scale = (self.width() - 2 * pad) / 24.0
        painter.translate(pad, pad)
        painter.scale(scale, scale)
        pen = QPen(color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        icon = self._icon

        if icon == "plus":
            painter.drawLine(QPointF(6.5, 12), QPointF(17.5, 12))
            painter.drawLine(QPointF(12, 6.5), QPointF(12, 17.5))
        elif icon == "trash":
            painter.drawLine(QPointF(5.5, 7.5), QPointF(18.5, 7.5))
            painter.drawPath(_path([(9.5, 7.5), (9.5, 5), (14.5, 5), (14.5, 7.5)]))
            painter.drawPath(
                _path([(7.5, 7.5), (8.5, 19), (15.5, 19), (16.5, 7.5)])
            )
            painter.drawLine(QPointF(10.5, 10.5), QPointF(10.9, 16))
            painter.drawLine(QPointF(13.5, 10.5), QPointF(13.1, 16))
        elif icon == "panel":
            painter.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2.5, 2.5)
            painter.drawLine(QPointF(14, 5), QPointF(14, 19))
            for y in (8.5, 12, 15.5):
                painter.drawLine(QPointF(15.8, y), QPointF(18.8, y))
        elif icon == "folder":
            painter.drawPath(
                _path(
                    [
                        (3.5, 18),
                        (3.5, 6.5),
                        (9.2, 6.5),
                        (11.4, 8.8),
                        (20.5, 8.8),
                        (20.5, 18),
                        (3.5, 18),
                    ]
                )
            )
        elif icon == "play":
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(_path([(8, 5.5), (18.5, 12), (8, 18.5), (8, 5.5)]))
        elif icon == "pause":
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(7.5, 5.5, 3.4, 13), 1, 1)
            painter.drawRoundedRect(QRectF(13.1, 5.5, 3.4, 13), 1, 1)
        elif icon == "prev":
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(6.5, 6, 2.4, 12), 1, 1)
            painter.drawPath(_path([(18, 6), (10, 12), (18, 18), (18, 6)]))
        elif icon == "next":
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(15.1, 6, 2.4, 12), 1, 1)
            painter.drawPath(_path([(6, 6), (14, 12), (6, 18), (6, 6)]))
        elif icon == "close":
            painter.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
            painter.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
        elif icon == "detach":
            painter.drawLine(QPointF(12, 4), QPointF(12, 15))
            painter.drawPath(_path([(8, 11), (12, 15.5), (16, 11)]))
            painter.drawLine(QPointF(6, 19), QPointF(18, 19))
        elif icon == "external":
            painter.drawRoundedRect(QRectF(4.5, 9.5, 10, 10), 1.5, 1.5)
            painter.drawPath(_path([(12.5, 11.5), (19.5, 4.5)]))
            painter.drawPath(_path([(14.5, 4.5), (19.5, 4.5), (19.5, 9.5)]))
        painter.restore()


def _path(points: list[tuple[float, float]]) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    return path


class FlowLayout(QLayout):
    """Reparto en líneas que se reajusta al ancho: las tarjetas se envuelven."""

    def __init__(
        self, parent: QWidget | None = None, margin: int = 0, spacing: int = 6
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:  # noqa: N802
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x, y = effective.x(), effective.y()
        line_height = 0
        gap = self.spacing()
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + gap
            if next_x - gap > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y += line_height + gap
                next_x = x + hint.width() + gap
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class ClipTile(QFrame):
    """Un clip del panel: extremos a la izquierda, ficha y acciones a la derecha."""

    activated = Signal(object)
    preview_requested = Signal(object)
    open_requested = Signal(object)
    detach_requested = Signal(object, bool)
    follow_toggled = Signal(object, bool)
    remove_requested = Signal(object)
    hovered = Signal(object)
    unhovered = Signal()

    def __init__(self, clip: GroupedClip, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ClipTile")
        self.clip = clip
        self._active = active
        self._zoom = 1.0
        self._thumb_side = 0
        self._detach_available = False
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        row = QHBoxLayout(self)
        row.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        row.setSpacing(CARD_COLUMN_GAP)

        thumbs = QWidget()
        head = QHBoxLayout(thumbs)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(CARD_THUMB_GAP)
        self.thumb_first = FrameThumb("primero", "1º")
        self.thumb_last = FrameThumb("último", "2º")
        self.arrow = QLabel("›")
        self.arrow.setStyleSheet(f"color: {COLORS['ink_muted']}; font-size: 13px;")
        head.addWidget(self.thumb_first)
        head.addWidget(self.arrow, alignment=Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self.thumb_last)
        row.addWidget(thumbs, alignment=Qt.AlignmentFlag.AlignTop)

        media = clip.media
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        self.name = ElidedLabel(media.label)
        self.name.setObjectName("TileName")
        self.name.setToolTip(str(media.path))
        title_row.addWidget(self.name, 1)
        self.btn_remove = IconButton("close", TILE_REMOVE_SIZE, "Quitar este clip de la lista")
        self.btn_remove.clicked.connect(self._request_remove)
        title_row.addWidget(self.btn_remove, alignment=Qt.AlignmentFlag.AlignTop)
        column.addLayout(title_row)

        self.detail = ElidedLabel(
            f"{media.resolution} · {media.fps_label} fps · {format_duration(media.duration)}"
        )
        self.detail.setObjectName("TileDetail")
        column.addWidget(self.detail)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(4)
        self.status = ElidedLabel()
        self.status.setObjectName("TileStatus")
        footer.addWidget(self.status, 1)

        self.btn_detach = IconButton(
            "detach",
            TILE_ACTION_SIZE,
            "Sacar este clip a una fila nueva justo debajo (vuelve a pulsar para devolverlo)",
            checkable=True,
        )
        self.btn_detach.toggled.connect(
            lambda checked: self.detach_requested.emit(self.clip.media.path, checked)
        )
        footer.addWidget(self.btn_detach)

        self.follow_switch = ToggleSwitch("Mostrar siguientes")
        self.follow_switch.setObjectName("FollowSwitch")
        self.follow_switch.setToolTip("Mostrar solo los clips que empiezan como termina este")
        self.follow_switch.toggled.connect(
            lambda checked: self.follow_toggled.emit(self.clip.media.path, checked)
        )
        footer.addWidget(self.follow_switch)

        self.btn_play = IconButton("play", TILE_ACTION_SIZE, "Previsualizar este clip")
        self.btn_play.clicked.connect(
            lambda: self.preview_requested.emit(self.clip.media)
        )
        footer.addWidget(self.btn_play)
        column.addLayout(footer)
        column.addStretch(1)
        row.addLayout(column, 1)

        self.apply_zoom(1.0)
        self.set_frames(clip.frames)
        self.set_active(active)
        self._refresh_status()

    # --- estado ---
    @property
    def tile_width(self) -> int:
        return self.width()

    def height_hint(self) -> int:
        return self.sizeHint().height()

    def apply_zoom(self, zoom: float) -> None:
        """Escala miniaturas y tipografía con el deslizador del panel."""
        self._zoom = clamp_zoom(zoom)
        width, side = card_metrics(self._zoom)
        self.setFixedWidth(width)
        if side != self._thumb_side:
            self._thumb_side = side
            self.thumb_first.set_fixed_side(side)
            self.thumb_last.set_fixed_side(side)
        title_px, detail_px, status_px = card_font_sizes(self._zoom)
        self.name.setStyleSheet(f"font-size: {title_px:.1f}px;")
        self.detail.setStyleSheet(f"font-size: {detail_px:.1f}px;")
        compact = self._zoom < 0.9
        self.detail.setVisible(not compact)
        self.status.setVisible(not compact)
        self.arrow.setVisible(side >= 40)
        self._refresh_status()
        self.updateGeometry()

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

    def set_detach_available(self, available: bool) -> None:
        """Un clip solo no puede separarse… salvo que ya lo esté (para deshacerlo)."""
        self._detach_available = available
        self.btn_detach.setEnabled(available or self.btn_detach.isChecked())

    def set_detach_checked(self, checked: bool) -> None:
        """Marca el clip como separado sin reemitir `detach_requested`."""
        button = self.btn_detach
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)
        button.setEnabled(self._detach_available or checked)

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

    def _refresh_status(self) -> None:
        if self.clip.problem:
            self.status.setText(self.clip.problem)
            color = COLORS["warn"]
        else:
            self.status.setText(format_size(self.clip.media.size_bytes))
            color = COLORS["ink_muted"]
        _, detail_px, status_px = card_font_sizes(self._zoom)
        self.status.setStyleSheet(f"font-size: {status_px:.1f}px; color: {color};")
        self.status.setToolTip(self.status.full_text())

    def _request_remove(self) -> None:
        self.remove_requested.emit(self.clip.media)

    # --- eventos ---
    def enterEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.unhovered.emit()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit(self.clip.media)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        open_action = menu.addAction("Abrir en el reproductor del sistema")
        menu.addSeparator()
        remove_action = menu.addAction("Quitar de la lista")
        chosen = menu.exec(event.globalPos())
        if chosen is open_action:
            self.open_requested.emit(self.clip.media)
        elif chosen is remove_action:
            self._request_remove()


class DragHandle(QLabel):
    """Asa de arrastre de la fila: inicia el reordenado sin pisar los clics del clip."""

    drag_started = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("⠿", parent)
        self.setObjectName("RowHandle")
        self.setFixedWidth(16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
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
    """Una fila = un grupo; sus tarjetas se envuelven en líneas según el ancho."""

    selected = Signal(int)
    play_requested = Signal(object)
    preview_requested = Signal(object)
    remove_requested = Signal(object)
    detach_requested = Signal(object, bool)
    follow_toggled = Signal(object, bool)
    hover_changed = Signal(object)

    def __init__(self, group: ClipGroup, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ClipRow")
        self.group = group
        self.index = index
        self._active = 0
        self._selected = False
        self._hovered = False
        self._tiles: list[ClipTile] = []
        self._relayouting = False
        self._layout_key: tuple[int, int, int] | None = None
        self._zoom = 1.0
        # Sin esto el hover se pierde al entrar en una tarjeta hija.
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(10)

        self.handle = DragHandle()
        self.handle.drag_started.connect(self.start_drag)
        layout.addWidget(self.handle, alignment=Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(6)
        column.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = ElidedLabel()
        self.title.setObjectName("RowTitle")
        self.tags = QLabel()
        self.tags.setObjectName("RowTags")
        head.addWidget(self.title, 1)
        head.addWidget(self.tags, 0)
        column.addLayout(head)

        self.strip = QWidget()
        self.flow = FlowLayout(self.strip, margin=0, spacing=8)
        column.addWidget(self.strip)
        layout.addLayout(column, 1)

        for member in group.members:
            tile = ClipTile(member, active=False)
            tile.activated.connect(self._on_tile_activated)
            tile.preview_requested.connect(self.preview_requested)
            tile.open_requested.connect(self.play_requested)
            tile.remove_requested.connect(self.remove_requested)
            tile.detach_requested.connect(self.detach_requested)
            tile.follow_toggled.connect(self.follow_toggled)
            tile.hovered.connect(self._on_tile_hovered)
            tile.unhovered.connect(self._on_tile_unhovered)
            tile.set_follow_available()
            tile.set_detach_available(len(group.members) > 1)
            self.flow.addWidget(tile)
            self._tiles.append(tile)

        self._refresh()
        self.relayout_tiles(force=True)
        # Segunda pasada diferida para cuando el alto real de la tarjeta cambia.
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(0)
        self._reflow_timer.timeout.connect(self._reflow_after_zoom)

    # --- estado ---
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout_tiles()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hovered(False)
        super().leaveEvent(event)

    def _set_hovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        # El montaje resalta el clip que aporta esta fila (el activo), no la variante 1.
        self.hover_changed.emit(self.active.media.path if hovered else None)

    def _on_tile_hovered(self, tile: ClipTile) -> None:
        """Al entrar en una tarjeta concreta se resalta esa, aunque no sea la activa."""
        if self._hovered:
            self.hover_changed.emit(tile.clip.media.path)

    def _on_tile_unhovered(self) -> None:
        if self._hovered:
            self.hover_changed.emit(self.active.media.path)

    @property
    def hovered(self) -> bool:
        return self._hovered

    def set_zoom(self, zoom: float) -> None:
        self._zoom = clamp_zoom(zoom)
        for tile in self._tiles:
            tile.apply_zoom(self._zoom)
        self._layout_key = None
        self.relayout_tiles(force=True)
        # El alto de una tarjeta solo es fiable con su layout ya activado:
        # una segunda pasada evita dejar hueco muerto al encoger el zoom.
        self._reflow_timer.start()

    def _reflow_after_zoom(self) -> None:
        self._layout_key = None
        self.relayout_tiles(force=True)

    def relayout_tiles(self, force: bool = False) -> None:
        """
        Reparte las tarjetas en líneas según el ancho disponible.

        A diferencia de la tira horizontal anterior, aquí no hay scroll propio:
        las tarjetas se envuelven y la fila crece en vertical. El alto de la
        banda se fija a mano (contando líneas) para no encadenar varios
        `heightForWidth` dentro del scroll de la lista.
        """
        if self._relayouting:
            return
        visible = self.visible_tiles
        if not visible:
            self.strip.setFixedHeight(0)
            return
        available = max(self.width() - ROW_SIDE_SPACE, 240)
        gap = self.flow.spacing()
        card_width = visible[0].width() or 240
        per_line = max(1, int((available + gap) // (card_width + gap)))
        lines = math.ceil(len(visible) / per_line)
        card_height = max(tile.height_hint() for tile in visible)
        key = (available, card_width, card_height)
        if key == self._layout_key and not force:
            return

        self._relayouting = True
        try:
            self._layout_key = key
            self.strip.setFixedHeight(lines * card_height + (lines - 1) * gap)
        finally:
            self._relayouting = False
        self.updateGeometry()

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
        if self._hovered:
            self.hover_changed.emit(self.active.media.path)

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
        self._layout_key = None
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
        if self._hovered:
            # El clip del montaje cambia sin salir de la fila.
            self.hover_changed.emit(self.active.media.path)

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
            self._set_tags(self.group.origin, COLORS["ok"])
        else:
            self.title.setText(f"{position} · {self.active.media.label}")
            color = COLORS["warn"] if self.active.problem else COLORS["ink_muted"]
            self._set_tags(self.group.origin, color)

        self.handle.setToolTip("Arrastra para reordenar la fila")

    def _set_tags(self, text: str, color: str) -> None:
        metrics = self.tags.fontMetrics()
        self.tags.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, 112))
        self.tags.setToolTip(text)
        self.tags.setStyleSheet(f"color: {color}; font-size: 10px;")

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
        self._layout.setSpacing(8)
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


class VariantChip(QAbstractButton):
    """Una variante de la ranura del timeline: su primer fotograma y su nombre."""

    def __init__(
        self,
        member: TimelineMember,
        position: int,
        current: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._member = member
        self._position = position
        self._current = current
        self._hovered = False
        self.setFixedSize(VARIANT_CHIP_W, VARIANT_CHIP_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{member.media.label} · {format_duration(member.media.duration)}"
        )
        self.setAccessibleName(f"Sustituir por {member.media.label}")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._cover = cover_pixmap(
            member.frames, VARIANT_CHIP_W - 2 * CARD_PADDING, VARIANT_THUMB_H
        )

    @property
    def position(self) -> int:
        return self._position

    @property
    def is_current(self) -> bool:
        return self._current

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self._current:
            edge, width = COLORS["accent"], 2
        elif self._hovered:
            edge, width = COLORS["accent_hi"], 2
        else:
            edge, width = COLORS["line"], 1
        painter.setPen(QPen(QColor(edge), width))
        painter.setBrush(
            QColor(COLORS["surface_hi"] if self._hovered else COLORS["surface_raised"])
        )
        painter.drawRoundedRect(rect, 6, 6)

        thumb = QRectF(
            CARD_PADDING,
            CARD_PADDING,
            self.width() - 2 * CARD_PADDING,
            VARIANT_THUMB_H,
        )
        if self._cover is not None and not self._cover.isNull():
            painter.save()
            path = QPainterPath()
            path.addRoundedRect(thumb, 4, 4)
            painter.setClipPath(path)
            painter.drawPixmap(thumb.toRect(), self._cover)
            painter.restore()
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["timeline_track"]))
            painter.drawRoundedRect(thumb, 4, 4)

        label = QRectF(
            CARD_PADDING,
            thumb.bottom() + 2,
            self.width() - 2 * CARD_PADDING,
            self.height() - thumb.bottom() - 4,
        )
        font = QFont(self.font())
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(
            QPen(QColor(COLORS["accent_hi"] if self._current else COLORS["ink"]), 1)
        )
        name = self.fontMetrics().elidedText(
            self._member.media.label, Qt.TextElideMode.ElideMiddle, int(label.width())
        )
        painter.drawText(
            label,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )


class VariantPopup(QWidget):
    """
    Bandeja flotante con las variantes de la ranura bajo el cursor.

    Se dibuja como hija de la ventana y no como ventana aparte: así aparece por
    encima del timeline y de la barra de salida sin depender de que el gestor de
    ventanas respete la posición pedida (Wayland ignora mover ventanas de nivel
    superior). Se cierra sola al salir el cursor del timeline y de la bandeja.
    """

    def __init__(self, strip: MontageStrip) -> None:
        super().__init__(None)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(True)
        self._strip = strip
        self._slot = -1
        self._chips: list[VariantChip] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame()
        self.card.setObjectName("Panel")
        outer.addWidget(self.card)
        inner = QVBoxLayout(self.card)
        inner.setContentsMargins(10, 8, 10, 10)
        inner.setSpacing(6)
        self.title = QLabel()
        self.title.setObjectName("SectionTitle")
        inner.addWidget(self.title)
        self._row_host = QWidget()
        self._row = QHBoxLayout(self._row_host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)
        inner.addWidget(self._row_host)

    @property
    def slot(self) -> int:
        return self._slot

    def chips(self) -> list[VariantChip]:
        return list(self._chips)

    def show_for(self, index: int, slot: TimelineSlot, anchor: QRect) -> None:
        host = self._strip.window()
        if self.parentWidget() is not host:
            self.setParent(host)
        if index == self._slot and not self.isHidden() and self._chips:
            return
        self._clear()
        self._slot = index
        self.title.setText(f"FILA {slot.row + 1} · ELIGE VARIANTE")
        for position, member in enumerate(slot.members):
            chip = VariantChip(member, position, position == slot.active)
            chip.clicked.connect(lambda _checked=False, pos=position: self.choose(pos))
            self._row.addWidget(chip)
            self._chips.append(chip)
        self.adjustSize()
        self._place(anchor)
        self.show()
        self.raise_()

    def choose(self, position: int) -> None:
        if self._slot < 0:
            return
        self._strip.request_variant(self._slot, position)

    def _place(self, anchor: QRect) -> None:
        """Coloca la bandeja centrada sobre el tramo, dentro de la ventana."""
        host = self.parentWidget()
        if host is None:
            return
        size = self.sizeHint()
        top_left = self._strip.mapTo(host, anchor.topLeft())
        bounds = host.rect()
        x = top_left.x() + anchor.width() // 2 - size.width() // 2
        x = max(bounds.left() + 4, min(x, bounds.right() - size.width() - 4))
        y = top_left.y() - size.height() - 10
        if y < bounds.top() + 4:
            y = top_left.y() + anchor.height() + 10
        self.setGeometry(x, y, size.width(), size.height())

    def _clear(self) -> None:
        for chip in self._chips:
            self._row.removeWidget(chip)
            chip.hide()
            # Sin `setParent(None)`: un chip huérfano sería una ventana de nivel superior.
            chip.deleteLater()
        self._chips.clear()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._strip.cancel_variant_hide()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._strip.schedule_variant_hide()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._slot = -1
        super().hideEvent(event)


class MontageStrip(QWidget):
    """
    Timeline del montaje: un tramo por clip, con su primer fotograma a sangre.

    El ancho de cada tramo es proporcional a la duración del clip activo. El
    cursor (playhead) recorre el montaje con la reproducción y se puede mover
    pulsando un punto del tramo. Los tramos se arrastran para reordenar las
    filas, y los que tienen variantes muestran una bandeja para cambiarlas
    encima del propio timeline.
    """

    segment_clicked = Signal(object, float)
    segment_moved = Signal(int, int)
    variant_chosen = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: list[TimelineSlot] = []
        self._rects: list[QRectF] = []
        self._covers: list[QPixmap | None] = []
        self._highlight: Path | None = None
        self._selected: Path | None = None
        self._position = 0.0
        self._press: QPointF | None = None
        self._drop_gap: int | None = None
        self._hover_slot: int | None = None
        self.setFixedHeight(MONTAGE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._popup = VariantPopup(self)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(VARIANT_HIDE_MS)
        self._hide_timer.timeout.connect(self.hide_variants_if_outside)

    # --- datos ---
    def set_slots(self, slots: list[TimelineSlot]) -> None:
        self._slots = list(slots)
        known = {slot.media.path for slot in self._slots}
        if self._highlight is not None and self._highlight not in known:
            self._highlight = None
        if self._selected is not None and self._selected not in known:
            self._selected = None
        self._position = min(self._position, self._total_duration())
        self._hover_slot = None
        self.hide_variants()
        self._layout()
        self.update()

    def set_clips(self, clips: list[tuple[MediaInfo, EdgeFrames | None]]) -> None:
        """Atajo para un montaje sin variantes: un miembro por tramo."""
        self.set_slots(
            [
                TimelineSlot(
                    row=index,
                    members=(TimelineMember(media, frames),),
                )
                for index, (media, frames) in enumerate(clips)
            ]
        )

    def slots(self) -> list[TimelineSlot]:
        return list(self._slots)

    def set_highlight(self, path: Path | None) -> None:
        """Ilumina el tramo del clip; `None` apaga el resaltado."""
        if path == self._highlight:
            return
        self._highlight = path
        label = next(
            (slot.media.label for slot in self._slots if slot.media.path == path), ""
        )
        self.setToolTip(label)
        self.update()

    def set_selected(self, path: Path | None) -> None:
        """Marca el tramo de la fila seleccionada; `None` lo apaga."""
        if path == self._selected:
            return
        self._selected = path
        self.update()

    def set_position(self, seconds: float) -> None:
        """Mueve el cursor del timeline (tiempo global del montaje)."""
        seconds = max(0.0, float(seconds))
        if abs(seconds - self._position) < 0.001:
            return
        self._position = seconds
        self.update()

    @property
    def position(self) -> float:
        return self._position

    @property
    def playhead_x(self) -> float | None:
        """Coordenada x del cursor; `None` si no hay clips."""
        return self._playhead_x()

    @property
    def highlighted(self) -> Path | None:
        return self._highlight

    @property
    def selected(self) -> Path | None:
        return self._selected

    @property
    def popup(self) -> VariantPopup:
        return self._popup

    @property
    def drop_gap(self) -> int | None:
        return self._drop_gap

    def clip_paths(self) -> list[Path]:
        return [slot.media.path for slot in self._slots]

    def rect_for(self, path: Path) -> QRectF | None:
        """Rectángulo de un clip; útil para pruebas y para depurar el reparto."""
        for slot, rect in zip(self._slots, self._rects, strict=True):
            if slot.media.path == path:
                return QRectF(rect)
        return None

    def slot_at(self, point: QPointF) -> int | None:
        """Índice del tramo que contiene el punto."""
        for index, rect in enumerate(self._rects):
            if rect.contains(point):
                return index
        return None

    def path_at(self, point: QPointF) -> Path | None:
        """Clip cuyo tramo contiene el punto, o `None` si se pulsa fuera."""
        index = self.slot_at(point)
        return None if index is None else self._slots[index].media.path

    def time_at(self, point: QPointF) -> tuple[Path, float] | None:
        """Clip y segundo local dentro de él para un punto del timeline."""
        index = self.slot_at(point)
        if index is None:
            return None
        rect = self._rects[index]
        slot = self._slots[index]
        if rect.width() <= 0:
            return None
        fraction = (point.x() - rect.x()) / rect.width()
        duration = slot.media.duration
        local = max(0.0, min(duration, fraction * duration))
        return slot.media.path, local

    def insertion_gap(self, x: float) -> int:
        """Hueco (0..n) donde caería un tramo soltado en `x`."""
        for index, rect in enumerate(self._rects):
            if x < rect.center().x():
                return index
        return len(self._rects)

    # --- variantes ---
    def request_variant(self, index: int, position: int) -> None:
        """Pide cambiar el clip activo de la ranura `index`."""
        if not (0 <= index < len(self._slots)):
            return
        slot = self._slots[index]
        if not (0 <= position < len(slot.members)) or position == slot.active:
            self.hide_variants()
            return
        self.variant_chosen.emit(index, position)
        self.hide_variants()

    def show_variants(self, index: int) -> None:
        if not (0 <= index < len(self._slots)) or index >= len(self._rects):
            return
        slot = self._slots[index]
        if not slot.has_variants:
            self.hide_variants()
            return
        anchor = self._rects[index].toRect()
        self._hide_timer.stop()
        self._popup.show_for(index, slot, anchor)

    def hide_variants(self) -> None:
        self._hide_timer.stop()
        if self._popup.isVisible():
            self._popup.hide()

    def schedule_variant_hide(self) -> None:
        if self._popup.isVisible():
            self._hide_timer.start()

    def cancel_variant_hide(self) -> None:
        self._hide_timer.stop()

    def hide_variants_if_outside(self) -> None:
        if not self._popup.isVisible():
            return
        if self._popup.rect().contains(self._popup.mapFromGlobal(QCursor.pos())):
            return
        self.hide_variants()

    def _update_hover(self, index: int | None) -> None:
        if index == self._hover_slot:
            return
        self._hover_slot = index
        self.update()
        if index is not None and self._slots[index].has_variants:
            self.show_variants(index)
            return
        self.hide_variants()

    # --- eventos de ratón ---
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        index = self.slot_at(event.position())
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if index is not None
            else Qt.CursorShape.ArrowCursor
        )
        if self._press is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            if (event.position() - self._press).manhattanLength() >= DRAG_THRESHOLD:
                source = self.slot_at(event.position())
                self._press = None
                if source is not None:
                    self._start_drag(source)
                return
        self._update_hover(index)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press = None
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.cancel_variant_hide()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._hover_slot = None
        self.schedule_variant_hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.slot_at(event.position())
            if index is not None:
                self._press = event.position()
                hit = self.time_at(event.position())
                if hit is not None:
                    self.segment_clicked.emit(hit[0], hit[1])
                event.accept()
                return
        super().mousePressEvent(event)

    # --- arrastre para reordenar ---
    def _start_drag(self, index: int) -> None:
        if not (0 <= index < len(self._rects)):
            return
        mime = QMimeData()
        mime.setData(TIMELINE_MIME, str(index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        rect = self._rects[index].toRect()
        if rect.isValid():
            drag.setPixmap(self.grab(rect))
        drag.exec(Qt.DropAction.MoveAction)
        self._drop_gap = None
        self.update()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasFormat(TIMELINE_MIME):
            self._drop_gap = self.insertion_gap(event.position().x())
            self.update()
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(TIMELINE_MIME):
            return
        self._drop_gap = self.insertion_gap(event.position().x())
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drop_gap = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasFormat(TIMELINE_MIME):
            return
        raw = bytes(event.mimeData().data(TIMELINE_MIME)).decode()
        try:
            source = int(raw)
        except ValueError:
            return
        gap = self.insertion_gap(event.position().x())
        self._drop_gap = None
        self.update()
        event.acceptProposedAction()
        self.segment_moved.emit(source, gap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # La bandeja está anclada a un tramo: al cambiar el reparto se recoloca sola.
        self.hide_variants()
        self._layout()

    # --- geometría ---
    def _total_duration(self) -> float:
        return sum(max(slot.media.duration, 0.001) for slot in self._slots)

    def _layout(self) -> None:
        available = max(self.width() - 2 * MONTAGE_EDGE, 1)
        widths = montage_widths([slot.media.duration for slot in self._slots], available)
        self._rects = []
        x = float(MONTAGE_EDGE)
        for width in widths:
            self._rects.append(QRectF(x, 2, width, MONTAGE_HEIGHT - MONTAGE_CAPTION_H - 6))
            x += width
        self._rebuild_covers()

    def _rebuild_covers(self) -> None:
        """Escala el primer fotograma de cada tramo a su recuadro (a sangre)."""
        self._covers = []
        for slot, rect in zip(self._slots, self._rects, strict=True):
            self._covers.append(
                cover_pixmap(slot.frames, int(rect.width()), int(rect.height()))
            )

    def _gap_x(self, gap: int) -> float:
        if not self._rects:
            return float(MONTAGE_EDGE)
        if gap <= 0:
            return self._rects[0].left()
        if gap >= len(self._rects):
            return self._rects[-1].right()
        return self._rects[gap].left()

    def _playhead_x(self) -> float | None:
        if not self._slots or not self._rects:
            return None
        remaining = self._position
        last = len(self._slots) - 1
        for index, (slot, rect) in enumerate(zip(self._slots, self._rects, strict=True)):
            duration = max(slot.media.duration, 0.001)
            if remaining <= duration or index == last:
                fraction = max(0.0, min(remaining / duration, 1.0))
                return rect.x() + fraction * rect.width()
            remaining -= duration
        return self._rects[-1].right()

    # --- pintura ---
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if not self._slots:
            painter.setPen(QPen(QColor(COLORS["ink_muted"]), 1))
            painter.drawText(
                QRectF(0, 0, self.width(), self.height()),
                Qt.AlignmentFlag.AlignCenter,
                "El timeline aparecerá aquí: añade clips para montar la secuencia",
            )
            return

        for index, rect in enumerate(self._rects):
            self._paint_segment(painter, index, rect)

        self._paint_played(painter)
        self._paint_playhead(painter)
        self._paint_drop_gap(painter)
        self._paint_caption(painter)

    def _paint_segment(self, painter: QPainter, index: int, rect: QRectF) -> None:
        slot = self._slots[index]
        hot = slot.media.path == self._highlight
        chosen = slot.media.path == self._selected
        hovered = index == self._hover_slot
        has_variants = slot.has_variants

        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.setClipPath(path)
        cover = self._covers[index] if index < len(self._covers) else None
        if cover is not None and not cover.isNull():
            painter.drawPixmap(rect.toRect(), cover)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["timeline_track"]))
            painter.drawRect(rect)
        painter.restore()

        if chosen:
            edge, width = COLORS["accent_hi"], 2
        elif hovered or hot or has_variants:
            edge, width = COLORS["accent"], 2
        else:
            edge, width = COLORS["line"], 1
        painter.setPen(QPen(QColor(edge), width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 3, 3)

        if chosen:
            bar = QRectF(rect.left() + 3, rect.bottom() - 4, max(rect.width() - 6, 1), 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["accent_hi"]))
            painter.drawRoundedRect(bar, 1, 1)

        if has_variants:
            self._paint_variant_badge(painter, slot, rect)

    def _paint_variant_badge(
        self, painter: QPainter, slot: TimelineSlot, rect: QRectF
    ) -> None:
        if rect.width() < 34 or rect.height() < 24:
            return
        badge = QRectF(rect.right() - 28, rect.top() + 4, 24, 14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(6, 10, 12, 200))
        painter.drawRoundedRect(badge, 3, 3)
        font = QFont(self.font())
        font.setPixelSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QPen(QColor(COLORS["accent_hi"]), 1))
        painter.drawText(
            badge,
            Qt.AlignmentFlag.AlignCenter,
            f"▾{len(slot.members)}",
        )

    def _paint_played(self, painter: QPainter) -> None:
        x = self._playhead_x()
        if x is None or x <= 0:
            return
        painter.save()
        painter.setClipRect(QRectF(0, 0, x, self.height()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(242, 167, 104, 34))
        for rect in self._rects:
            painter.drawRoundedRect(rect, 3, 3)
        painter.restore()

    def _paint_playhead(self, painter: QPainter) -> None:
        x = self._playhead_x()
        if x is None:
            return
        top = 2.0
        bottom = MONTAGE_HEIGHT - MONTAGE_CAPTION_H - 4
        painter.setPen(
            QPen(
                QColor(COLORS["playhead"]),
                MONTAGE_PLAYHEAD_W,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        handle = QPolygonF(
            [
                QPointF(x - 4.5, top),
                QPointF(x + 4.5, top),
                QPointF(x, top + 6.5),
            ]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["playhead"]))
        painter.drawPolygon(handle)

    def _paint_drop_gap(self, painter: QPainter) -> None:
        if self._drop_gap is None or not self._rects:
            return
        x = self._gap_x(self._drop_gap)
        top = 2.0
        bottom = MONTAGE_HEIGHT - MONTAGE_CAPTION_H - 4
        painter.setPen(QPen(QColor(COLORS["accent_hi"]), 2))
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["accent_hi"]))
        painter.drawPolygon(
            QPolygonF(
                [QPointF(x - 4, top), QPointF(x + 4, top), QPointF(x, top + 6)]
            )
        )

    def _paint_caption(self, painter: QPainter) -> None:
        baseline = QRectF(
            MONTAGE_EDGE,
            self.height() - MONTAGE_CAPTION_H,
            self.width() - 2 * MONTAGE_EDGE,
            MONTAGE_CAPTION_H,
        )
        total = self._total_duration()
        variants = sum(1 for slot in self._slots if slot.has_variants)
        summary = f"{len(self._slots)} clips · {format_duration(total)}"
        if variants:
            summary += f" · {variants} con variantes"
        painter.setPen(QPen(QColor(COLORS["ink_muted"]), 1))
        painter.drawText(
            baseline,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            summary,
        )
        suffix = ""
        if self._highlight is not None:
            name = next(
                (slot.media.label for slot in self._slots if slot.media.path == self._highlight),
                "",
            )
            if name:
                short = name if len(name) <= 26 else name[:25] + "…"
                suffix = f" · resaltado {short}"
        mono = QFont(self.font())
        mono.setFamilies(
            ["IBM Plex Mono", "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", "monospace"]
        )
        mono.setPixelSize(10)
        painter.setFont(mono)
        painter.setPen(QPen(QColor(COLORS["accent_hi"]), 1))
        painter.drawText(
            baseline,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"cursor {format_timecode(self._position)}{suffix}",
        )
