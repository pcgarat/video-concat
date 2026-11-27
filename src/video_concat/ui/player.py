"""Previsualización del montaje: reproductor que encadena los clips del timeline."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from video_concat.core.frames import EdgeFrames
from video_concat.core.probe import MediaInfo
from video_concat.logutil import get_logger
from video_concat.ui.formatting import format_timecode
from video_concat.ui.styles import COLORS
from video_concat.ui.timeline import TimelineMap
from video_concat.ui.widgets import IconButton

try:  # PySide6 completo trae QtMultimedia; la app debe seguir abriendo sin él.
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    MULTIMEDIA_AVAILABLE = True
except ImportError:  # pragma: no cover - depende del wheel instalado
    MULTIMEDIA_AVAILABLE = False

log = get_logger("player")
DEFAULT_ASPECT = 16 / 9


class VideoStage(QWidget):
    """Hueco del reproductor: pinta el marco 16:9 y centra el vídeo dentro."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aspect = DEFAULT_ASPECT
        self._title = "Nada que previsualizar"
        self._subtitle = "Añade MP4 al panel de clips"
        self._video: QWidget | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(240)

    def attach_video(self, video: QWidget) -> None:
        self._video = video
        video.setParent(self)
        video.hide()

    def set_aspect(self, aspect: float) -> None:
        if aspect <= 0.05:
            aspect = DEFAULT_ASPECT
        if abs(aspect - self._aspect) < 0.0001:
            return
        self._aspect = aspect
        self._place_video()

    def set_message(self, title: str, subtitle: str = "") -> None:
        self._title = title
        self._subtitle = subtitle
        self.update()

    def show_video(self, visible: bool) -> None:
        if self._video is None:
            return
        self._video.setVisible(visible)
        if visible:
            self._place_video()

    def _screen_rect(self) -> QRectF:
        margin = 8.0
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        if width <= 0 or height <= 0:
            return QRectF()
        if width / height > self._aspect:
            width = height * self._aspect
        else:
            height = width / self._aspect
        return QRectF(
            (self.width() - width) / 2, (self.height() - height) / 2, width, height
        )

    def _place_video(self) -> None:
        if self._video is None:
            return
        rect = self._screen_rect().adjusted(1, 1, -1, -1)
        self._video.setGeometry(rect.toRect())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_video()

    def paintEvent(self, _event) -> None:  # noqa: N802
        rect = self._screen_rect()
        if rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS["line"]), 1))
        painter.setBrush(QColor(COLORS["stage"]))
        painter.drawRoundedRect(rect, 8, 8)

        if self._video is not None and self._video.isVisible():
            return
        text_rect = rect.adjusted(16, 0, -16, 0)
        title_font = QFont(self.font())
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QPen(QColor(COLORS["ink"]), 1))
        painter.drawText(
            text_rect.translated(0, -8),
            Qt.AlignmentFlag.AlignCenter,
            self._title,
        )
        if self._subtitle:
            sub_font = QFont(self.font())
            sub_font.setPixelSize(11)
            painter.setFont(sub_font)
            painter.setPen(QPen(QColor(COLORS["ink_muted"]), 1))
            painter.drawText(
                text_rect.translated(0, 16),
                Qt.AlignmentFlag.AlignCenter,
                self._subtitle,
            )


class PreviewPlayer(QWidget):
    """
    Reproduce el timeline completo seguido.

    `QMediaPlayer` reproduce un fichero por vez, así que el montaje se encadena
    clip a clip al llegar a `EndOfMedia`. La interfaz habla siempre en tiempo
    global: `TimelineMap` traduce posición global ↔ clip local.
    """

    position_changed = Signal(float)
    clip_changed = Signal(int)
    playing_changed = Signal(bool)
    message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clips: list[tuple[MediaInfo, EdgeFrames | None]] = []
        self._map = TimelineMap([])
        self._index = 0
        self._loaded_index = -1
        self._global = 0.0
        self._playing = False
        self._engaged = False
        self._pending_local: float | None = None
        self._want_play = False
        self._player = None
        self._audio = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.stage = VideoStage()
        self.stage.setToolTip(
            "Previsualización del montaje: clic para reproducir o pausar.\n"
            "Los clips se encadenan al terminar cada uno."
        )
        self._video: QWidget | None = None
        if MULTIMEDIA_AVAILABLE:
            self._video = QVideoWidget()
            self.stage.attach_video(self._video)
            self._video.installEventFilter(self)
        self.stage.installEventFilter(self)
        layout.addWidget(self.stage, 1)

        transport = QHBoxLayout()
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(10)
        transport.addStretch(1)
        self.btn_prev = IconButton("prev", 30, "Clip anterior")
        self.btn_prev.clicked.connect(self.previous_clip)
        self.btn_play = IconButton("play", 46, "Reproducir el montaje", filled=True)
        self.btn_play.clicked.connect(self.toggle)
        self.btn_next = IconButton("next", 30, "Clip siguiente")
        self.btn_next.clicked.connect(self.next_clip)
        self.timecode = QLabel("00:00 / 00:00")
        self.timecode.setObjectName("Timecode")
        self.timecode.setMinimumWidth(124)
        self.timecode.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clip_label = QLabel("Sin clips")
        self.clip_label.setObjectName("Hint")
        self.clip_label.setMinimumWidth(150)
        transport.addWidget(self.btn_prev)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_next)
        transport.addSpacing(8)
        transport.addWidget(self.timecode)
        transport.addSpacing(8)
        transport.addWidget(self.clip_label)
        transport.addStretch(1)
        layout.addLayout(transport)

        self._sync_transport()

    # --- API ---
    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """Clic sobre la imagen o el hueco del vídeo: play/pausa."""
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.toggle()
            return True
        return super().eventFilter(watched, event)

    @property
    def clips(self) -> list[MediaInfo]:
        return [media for media, _frames in self._clips]

    @property
    def total(self) -> float:
        return self._map.total

    @property
    def position(self) -> float:
        return self._global

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def current_index(self) -> int:
        return self._index

    def set_clips(self, clips: list[tuple[MediaInfo, EdgeFrames | None]]) -> None:
        previous_path = self.current_path
        previous_local = self._local_position()
        previous_paths = [media.path for media, _frames in self._clips]
        self._clips = list(clips)
        self._map = TimelineMap([(media.path, media.duration) for media, _frames in clips])

        if not self._clips:
            self._release()
            self.stage.set_message("Nada que previsualizar", "Añade MP4 al panel de clips")
            self._set_global(0.0)
            self._sync_transport()
            return

        self._apply_aspect()
        index = (
            self._map.index_of(previous_path) if previous_path is not None else None
        )
        if index is not None:
            self._index = index
            local = min(previous_local, self._map.durations[index])
            self._global = self._map.time_of(index, local)
        else:
            self._index = min(self._index, len(self._clips) - 1)
            local = 0.0
            self._global = self._map.start(self._index)

        new_paths = [media.path for media, _frames in self._clips]
        if self._engaged and new_paths != previous_paths:
            self._load(self._index, local, autoplay=self._playing)
        elif not self._engaged:
            self.stage.set_message(
                f"{len(self._clips)} clip(s) en el montaje",
                "Pulsa play para ver la secuencia completa",
            )
        self._set_global(self._global, force=True)
        self._sync_transport()

    def toggle(self) -> None:
        """Play/pausa del montaje completo."""
        if not self._clips or not self._ensure_backend():
            return
        if self._playing:
            self._player.pause()
            return
        index = self._index
        local = self._local_position()
        if self._global >= self._map.total - 0.05:
            index, local = 0, 0.0
        if self._engaged and index == self._loaded_index and abs(
            local - self._loaded_local()
        ) < 0.5:
            self._player.play()
            return
        self._load(index, local, autoplay=True)

    def seek_to_clip(self, path: Path, local: float = 0.0) -> None:
        index = self._map.index_of(path)
        if index is None:
            return
        self._seek_index(index, local)

    def seek_global(self, seconds: float) -> None:
        if not self._clips:
            return
        index = self._map.index_at(seconds)
        self._seek_index(index, self._map.local_time(index, seconds))

    def preview(self, path: Path) -> None:
        """Reproduce un clip concreto desde el principio."""
        index = self._map.index_of(path)
        if index is None:
            return
        self._seek_index(index, 0.0, autoplay=True)

    def next_clip(self) -> None:
        if not self._clips:
            return
        self._seek_index(min(self._index + 1, len(self._clips) - 1), 0.0,
                         autoplay=self._playing)

    def previous_clip(self) -> None:
        if not self._clips:
            return
        self._seek_index(max(self._index - 1, 0), 0.0, autoplay=self._playing)

    def stop(self) -> None:
        self._release()
        self._set_global(0.0, force=True)
        self._sync_transport()

    @property
    def current_path(self) -> Path | None:
        if not self._clips:
            return None
        index = max(0, min(self._index, len(self._clips) - 1))
        return self._clips[index][0].path

    # --- backend ---
    def _ensure_backend(self) -> bool:
        if self._player is not None:
            return True
        if not MULTIMEDIA_AVAILABLE:
            self.stage.set_message(
                "Reproductor no disponible", "Falta QtMultimedia en esta instalación"
            )
            self.message.emit("QtMultimedia no está disponible")
            return False
        self._player = QMediaPlayer(self)
        if self._video is not None:
            self._player.setVideoOutput(self._video)
        try:
            self._audio = QAudioOutput(self)
            self._audio.setVolume(0.8)
            self._player.setAudioOutput(self._audio)
        except Exception:  # noqa: BLE001 - sin tarjeta de sonido el vídeo debe seguir
            log.warning("Sin salida de audio para la previsualización")
            self._audio = None
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.errorOccurred.connect(self._on_error)
        return True

    def _load(self, index: int, local: float, autoplay: bool) -> None:
        if not self._ensure_backend() or not self._clips:
            return
        index = max(0, min(index, len(self._clips) - 1))
        self._index = index
        self._loaded_index = index
        self._engaged = True
        self._pending_local = max(0.0, local)
        self._want_play = autoplay
        self.stage.show_video(True)
        self._player.setSource(
            QUrl.fromLocalFile(str(self._clips[index][0].path))
        )
        if autoplay:
            self._player.play()
        self._set_global(self._map.time_of(index, local), force=True)
        self.clip_changed.emit(index)
        self._sync_transport()

    def _release(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        self._engaged = False
        self._playing = False
        self._loaded_index = -1
        self._pending_local = None
        self.stage.show_video(False)
        self._sync_transport()

    def _seek_index(self, index: int, local: float, autoplay: bool | None = None) -> None:
        if not self._clips:
            return
        index = max(0, min(index, len(self._clips) - 1))
        local = max(0.0, min(local, self._map.durations[index]))
        self._index = index
        self._set_global(self._map.time_of(index, local), force=True)

        if not self._engaged:
            if autoplay:
                self._load(index, local, autoplay=True)
            else:
                self.clip_changed.emit(index)
                self._sync_transport()
            return

        if index == self._loaded_index and self._player is not None:
            ready = (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
            if self._player.mediaStatus() in ready:
                self._player.setPosition(int(local * 1000))
            else:
                self._pending_local = local
            want_play = self._playing if autoplay is None else autoplay
            playing_state = QMediaPlayer.PlaybackState.PlayingState
            if want_play and self._player.playbackState() != playing_state:
                self._player.play()
        else:
            self._load(
                index,
                local,
                autoplay=self._playing if autoplay is None else autoplay,
            )
        self.clip_changed.emit(index)
        self._sync_transport()

    def _local_position(self) -> float:
        if not self._map:
            return 0.0
        return max(0.0, self._global - self._map.start(self._index))

    def _loaded_local(self) -> float:
        if self._player is None or not self._map:
            return 0.0
        return max(0.0, self._player.position() / 1000.0)

    def _apply_aspect(self) -> None:
        if not self._clips:
            return
        video = self._clips[0][0].video
        if video and video.width and video.height:
            self.stage.set_aspect(video.width / video.height)
        else:
            self.stage.set_aspect(DEFAULT_ASPECT)

    # --- callbacks de QtMultimedia ---
    def _on_position_changed(self, position_ms: int) -> None:
        if self._loaded_index < 0 or not self._map:
            return
        local = max(0, position_ms) / 1000.0
        self._index = self._loaded_index
        self._set_global(self._map.time_of(self._loaded_index, local))

    def _on_media_status(self, status) -> None:
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ) and self._pending_local is not None:
            self._player.setPosition(int(self._pending_local * 1000))
            self._pending_local = None
            if self._want_play:
                self._player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            QTimer.singleShot(0, self._advance)

    def _advance(self) -> None:
        if not self._clips:
            return
        if self._index + 1 < len(self._clips):
            log.info("Previsualización: pasa al clip %d", self._index + 2)
            self._load(self._index + 1, 0.0, autoplay=True)
        else:
            self._playing = False
            self._set_global(self._map.total, force=True)
            self._sync_transport()

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing == self._playing:
            return
        self._playing = playing
        self._sync_transport()
        self.playing_changed.emit(playing)

    def _on_error(self, _error, message: str) -> None:
        log.error("Reproductor: %s", message)
        self.message.emit(f"No se pudo previsualizar: {message}")

    # --- estado visual ---
    def _set_global(self, seconds: float, force: bool = False) -> None:
        total = self._map.total
        value = max(0.0, min(float(seconds), total)) if total else 0.0
        changed = abs(value - self._global) >= 0.001
        self._global = value
        self._sync_transport()
        if changed or force:
            self.position_changed.emit(value)

    def _sync_transport(self) -> None:
        self.timecode.setText(
            f"{format_timecode(self._global)} / {format_timecode(self._map.total)}"
        )
        if self._clips:
            index = max(0, min(self._index, len(self._clips) - 1))
            label = self._clips[index][0].label
            text = f"{index + 1} / {len(self._clips)} · {label}"
            self.clip_label.setText(
                self.clip_label.fontMetrics().elidedText(
                    text, Qt.TextElideMode.ElideRight, 220
                )
            )
            self.clip_label.setToolTip(str(self._clips[index][0].path))
        else:
            self.clip_label.setText("Sin clips")
            self.clip_label.setToolTip("")
        self.btn_play.set_icon("pause" if self._playing else "play")
        self.btn_play.setToolTip("Pausar" if self._playing else "Reproducir el montaje")
        has_clips = bool(self._clips)
        for button in (self.btn_play, self.btn_prev, self.btn_next):
            button.setEnabled(has_clips)
