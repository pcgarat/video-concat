"""Ventana principal."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from video_concat.core.compatibility import CompatibilityReport, analyze_compatibility
from video_concat.core.concat import ConcatError, ConcatJob, ConcatResult, Pipeline, select_pipeline
from video_concat.core.frames import DEFAULT_THRESHOLD, EdgeFrames, extract_many
from video_concat.core.gpu import GpuCapabilities, detect_gpu
from video_concat.core.grouping import ClipGroup, follower_paths, group_clips
from video_concat.core.probe import MediaInfo, ProbeError, probe_file
from video_concat.logutil import get_logger
from video_concat.ui.formatting import format_duration
from video_concat.ui.player import PreviewPlayer
from video_concat.ui.styles import APP_STYLE, COLORS
from video_concat.ui.timeline import TimelineMember, TimelineSlot
from video_concat.ui.widgets import (
    ClipGroupRow,
    ElidedLabel,
    GroupRowList,
    IconButton,
    MontageStrip,
    ToggleSwitch,
)

VIDEO_FILTER = "Vídeos MP4 (*.mp4 *.MP4)"
# Espera máxima por hilo al cerrar; nunca se mata el hilo (dejaría ffmpeg huérfanos).
CLOSE_WAIT_MS = 15000
THRESHOLDS = (
    ("0.97 · solo idénticos", 0.97),
    ("0.93 · recomendado", 0.93),
    ("0.88 · permisivo", 0.88),
)
PANEL_DEFAULT_WIDTH = 420
PANEL_MIN_WIDTH = 380
PANEL_MAX_WIDTH = 640
log = get_logger("ui")


class ProbeWorker(QObject):
    progress = Signal(float, str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.paths = paths

    @Slot()
    def run(self) -> None:
        log.info("ProbeWorker arranca (%d ficheros)", len(self.paths))
        results: list[MediaInfo] = []
        total = len(self.paths) or 1
        try:
            for i, path in enumerate(self.paths):
                self.progress.emit(i / total, f"Analizando {path.name}…")
                log.info("ffprobe → %s", path)
                results.append(probe_file(path))
                log.info(
                    "ok %s (%s, %.2fs)",
                    path.name,
                    results[-1].resolution,
                    results[-1].duration,
                )
            self.progress.emit(1.0, "Análisis completado")
            log.info("ProbeWorker terminó OK")
            self.finished.emit(results)
        except ProbeError as exc:
            log.error("ProbeWorker ProbeError: %s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("ProbeWorker error inesperado")
            self.failed.emit(f"Error al analizar: {exc}")


class FramesWorker(QObject):
    progress = Signal(float, str)
    # object: las claves son Path y Qt no sabe convertir dict con claves no-String.
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, entries: list[tuple[Path, float]]) -> None:
        super().__init__()
        self.entries = entries
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Pide parar entre clips; `QThread.quit()` no basta sin event loop."""
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        log.info("FramesWorker arranca (%d clips)", len(self.entries))
        total = len(self.entries) or 1

        def report(done: int, _total: int) -> None:
            self.progress.emit(done / total, f"Fotogramas {done}/{total}")

        self.progress.emit(0.0, "Comparando primer y último fotograma…")
        try:
            results = extract_many(
                self.entries, on_progress=report, cancel=self._cancel
            )
            log.info("FramesWorker OK (%d clips)", len(results))
            self.finished.emit(results)
        except Exception as exc:  # noqa: BLE001
            log.exception("FramesWorker error inesperado")
            # Un fallo de la tanda no puede parecer «todo listo»: se distingue
            # de un resultado vacío legítimo con una señal propia.
            self.failed.emit(f"No se pudieron comparar los fotogramas: {exc}")


class ConcatWorker(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        items: list[MediaInfo],
        output: Path,
        report: CompatibilityReport,
        gpu: GpuCapabilities,
    ) -> None:
        super().__init__()
        self.job = ConcatJob(items, output, report, on_progress=self._on_progress, gpu=gpu)

    def _on_progress(self, value: float, message: str) -> None:
        self.progress.emit(value, message)

    @Slot()
    def run(self) -> None:
        log.info("ConcatWorker arranca → %s", self.job.output)
        try:
            result = self.job.run()
            log.info(
                "ConcatWorker OK pipeline=%s encoder=%s",
                result.pipeline,
                result.used_encoder,
            )
            self.finished.emit(result)
        except ConcatError as exc:
            log.error("ConcatWorker ConcatError: %s", exc)
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("ConcatWorker error inesperado")
            self.failed.emit(f"Error inesperado: {exc}")

    def cancel(self) -> None:
        log.info("ConcatWorker cancel solicitado")
        self.job.cancel()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Video Concat")
        self.resize(1280, 820)
        self.setMinimumSize(1020, 700)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)

        self._media: dict[Path, MediaInfo] = {}
        self._frames: dict[Path, EdgeFrames | Exception] = {}
        self._groups: list[ClipGroup] = []
        self._threshold = DEFAULT_THRESHOLD
        self._order: list[Path] = []
        # Orden automático por fotogramas; apagado por defecto, manda el orden manual.
        self._auto_sort = False
        # Clips sacados de su fila a mano: el agrupado automático no los reabsorbe.
        self._detached: set[Path] = set()
        self._follow_anchor: Path | None = None
        self._report: CompatibilityReport | None = None
        self._gpu = detect_gpu()
        self._probe_thread: QThread | None = None
        self._probe_worker: ProbeWorker | None = None
        self._frames_thread: QThread | None = None
        self._frames_worker: FramesWorker | None = None
        self._concat_thread: QThread | None = None
        self._concat_worker: ConcatWorker | None = None
        self._busy = False
        self._concatenating = False
        self._zoom = 1.0
        self._panel_width = PANEL_DEFAULT_WIDTH
        log.info("GPU detectada: %s (cuda_full=%s)", self._gpu, self._gpu.cuda_full)

        self._build_ui()
        self._update_mode_panel()
        self._update_actions()

    # --- construcción ---
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(11)

        header = QHBoxLayout()
        header.setSpacing(12)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(0)
        brand = QLabel("VIDEO CONCAT")
        brand.setObjectName("Brand")
        tagline = QLabel("Sala de montaje · previsualiza la secuencia completa")
        tagline.setObjectName("Tagline")
        brand_col.addWidget(brand)
        brand_col.addWidget(tagline)
        header.addLayout(brand_col, stretch=1)

        self.gpu_label = QLabel()
        self.gpu_label.setObjectName("StatusPill")
        self.gpu_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.gpu_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._paint_gpu_pill()

        self.btn_panel = IconButton(
            "panel", 32, "Mostrar u ocultar el panel de clips", checkable=True
        )
        self.btn_panel.setChecked(True)
        self.btn_panel.toggled.connect(self._toggle_side_panel)
        header.addWidget(self.btn_panel, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        self.body = QSplitter(Qt.Orientation.Horizontal)
        self.body.setObjectName("Body")
        self.body.setChildrenCollapsible(False)
        self.body.setHandleWidth(10)

        player_host = QWidget()
        player_layout = QVBoxLayout(player_host)
        player_layout.setContentsMargins(0, 0, 0, 0)
        self.player = PreviewPlayer()
        player_layout.addWidget(self.player)
        self.body.addWidget(player_host)

        self.side_panel = self._build_side_panel()
        self.body.addWidget(self.side_panel)
        self.body.setStretchFactor(0, 1)
        self.body.setStretchFactor(1, 0)
        self.body.setSizes([1280 - PANEL_DEFAULT_WIDTH - 40, PANEL_DEFAULT_WIDTH])
        layout.addWidget(self.body, stretch=1)

        layout.addWidget(self._build_timeline())
        layout.addWidget(self._build_bottom_bar())

        self.player.position_changed.connect(self.montage.set_position)
        self.player.clip_changed.connect(self._on_player_clip_changed)
        self.player.message.connect(self._set_status)

        open_act = QAction("Abrir…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._add_files)
        self.addAction(open_act)

        delete_act = QAction("Quitar el clip seleccionado", self)
        delete_act.setShortcut(QKeySequence.StandardKey.Delete)
        delete_act.triggered.connect(self._remove_selection)
        self.addAction(delete_act)

    def _build_timeline(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("TimelineFrame")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        title = QLabel("TIMELINE")
        title.setObjectName("SectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.selection_detail = QLabel("Sin clips")
        self.selection_detail.setObjectName("Hint")
        self.selection_detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        head.addWidget(self.selection_detail)
        layout.addLayout(head)

        self.montage = MontageStrip()
        self.montage.setToolTip(
            "Timeline del montaje: pulsa un punto para llevar el cursor, arrastra un "
            "tramo para reordenar su fila y pasa por los tramos con borde naranja "
            "para cambiar de variante"
        )
        self.montage.segment_clicked.connect(self._on_montage_segment_clicked)
        self.montage.segment_moved.connect(self._on_timeline_moved)
        self.montage.variant_chosen.connect(self._on_variant_chosen)
        layout.addWidget(self.montage)
        return frame

    def _build_bottom_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("BottomBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        outer.addWidget(self.progress)

        inner = QVBoxLayout()
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        out_title = QLabel("SALIDA")
        out_title.setObjectName("SectionTitle")
        row.addWidget(out_title)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("ruta/del/resultado.mp4")
        self.output_edit.textChanged.connect(lambda _t: self._update_actions())
        row.addWidget(self.output_edit, 1)
        self.btn_browse_out = IconButton("folder", 30, "Elegir el destino del vídeo")
        self.btn_browse_out.clicked.connect(self._browse_output)
        row.addWidget(self.btn_browse_out)
        self.btn_run = QPushButton("Concatenar")
        self.btn_run.setObjectName("Primary")
        self.btn_run.clicked.connect(self._on_run_clicked)
        row.addWidget(self.btn_run)
        inner.addLayout(row)

        info = QHBoxLayout()
        info.setSpacing(10)
        self.mode_pill = QLabel("—")
        self.mode_pill.setObjectName("StatusPill")
        info.addWidget(self.mode_pill)
        self.mode_detail = ElidedLabel("Añade al menos dos MP4.")
        self.mode_detail.setObjectName("Hint")
        self.mode_detail.setMinimumWidth(140)
        info.addWidget(self.mode_detail, 1)
        self.status_label = ElidedLabel("Listo")
        self.status_label.setObjectName("Hint")
        self.status_label.setMinimumWidth(230)
        self.status_label.setMaximumWidth(380)
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        info.addWidget(self.status_label)
        inner.addLayout(info)
        outer.addLayout(inner)
        return bar

    def _build_side_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setMinimumWidth(PANEL_MIN_WIDTH)
        panel.setMaximumWidth(PANEL_MAX_WIDTH)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(2)
        self.btn_add = IconButton("plus", 30, "Añadir vídeos…")
        self.btn_add.clicked.connect(self._add_files)
        self.btn_clear = IconButton("trash", 30, "Vaciar la lista")
        self.btn_clear.clicked.connect(self._clear_all)
        toolbar.addWidget(self.btn_add)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch(1)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(75, 140)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(104)
        self.zoom_slider.setPageStep(5)
        self.zoom_slider.setToolTip("Tamaño de las tarjetas del panel")
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        toolbar.addWidget(self.zoom_slider, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(toolbar)

        list_head = QHBoxLayout()
        list_head.setSpacing(8)
        self.list_title = QLabel("CLIPS · VACÍO")
        self.list_title.setObjectName("SectionTitle")
        list_head.addWidget(self.list_title)
        list_head.addStretch(1)
        self.btn_show_all = QPushButton("Mostrar todo")
        self.btn_show_all.setObjectName("Ghost")
        self.btn_show_all.setToolTip("Quitar el filtro «mostrar siguientes»")
        self.btn_show_all.setVisible(False)
        self.btn_show_all.clicked.connect(self._clear_follow_filter)
        list_head.addWidget(self.btn_show_all)
        layout.addLayout(list_head)

        options = QHBoxLayout()
        options.setSpacing(8)
        sort_label = QLabel("Orden automático")
        sort_label.setObjectName("Hint")
        options.addWidget(sort_label)
        self.sort_switch = ToggleSwitch("Orden automático")
        self.sort_switch.setChecked(self._auto_sort)
        self.sort_switch.setToolTip(
            "Apagado: el orden es el tuyo; los clips nuevos se añaden al final.\n"
            "Encendido: al añadir clips se encadenan por fotogramas y el orden "
            "automático manda."
        )
        self.sort_switch.toggled.connect(self._on_sort_toggled)
        options.addWidget(self.sort_switch, alignment=Qt.AlignmentFlag.AlignVCenter)
        options.addStretch(1)
        self.threshold_box = QComboBox()
        for label, value in THRESHOLDS:
            self.threshold_box.addItem(label, value)
        self.threshold_box.setCurrentIndex(1)
        self.threshold_box.setFixedWidth(164)
        self.threshold_box.setToolTip("Parecido mínimo entre fotogramas para agrupar clips")
        self.threshold_box.currentIndexChanged.connect(self._on_threshold_changed)
        options.addWidget(self.threshold_box)
        layout.addLayout(options)

        self.rows = GroupRowList()
        self.rows.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.rows.rows_reordered.connect(self._on_rows_reordered)
        self.rows.hide()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.rows)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll.hide()
        layout.addWidget(self.scroll, 1)

        self.empty_hint = QLabel(
            "Arrastra MP4 aquí o pulsa +.\n\n"
            "Cada fila reúne los clips que abren y cierran con el mismo fotograma.\n"
            "Solo se concatena el clip activo de cada fila; el interruptor del pie "
            "deja a la vista los clips que siguen al suyo."
        )
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setObjectName("Hint")
        self.empty_hint.setWordWrap(True)
        layout.addWidget(self.empty_hint, 1)
        return panel

    # --- tema ---
    def _paint_gpu_pill(self) -> None:
        if self._gpu.cuda_full:
            self.gpu_label.setText("GPU · CUDA full")
            self.gpu_label.setStyleSheet(
                f"background-color: {COLORS['ok_soft']}; color: {COLORS['ok']};"
            )
            tip = "NVDEC + scale_cuda + h264_nvenc"
        elif self._gpu.nvenc:
            self.gpu_label.setText("GPU · NVENC")
            self.gpu_label.setStyleSheet(
                f"background-color: {COLORS['ok_soft']}; color: {COLORS['ok']};"
            )
            tip = "h264_nvenc listo; CUDA full no disponible"
            if self._gpu.reasons:
                tip += " — " + "; ".join(self._gpu.reasons)
        else:
            self.gpu_label.setText("CPU · sin NVENC")
            self.gpu_label.setStyleSheet(
                f"background-color: {COLORS['warn_soft']}; color: {COLORS['warn']};"
            )
            tip = "; ".join(self._gpu.reasons) or "GPU no usable"
        self.gpu_label.setToolTip(tip)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # --- panel lateral ---
    @Slot(bool)
    def _toggle_side_panel(self, visible: bool) -> None:
        if visible:
            sizes = self.body.sizes()
            self.side_panel.show()
            available = max(sum(sizes), self.body.width())
            width = min(max(self._panel_width, PANEL_MIN_WIDTH), PANEL_MAX_WIDTH)
            self.body.setSizes([max(available - width, 320), width])
        else:
            sizes = self.body.sizes()
            if len(sizes) == 2 and sizes[1] > 0:
                self._panel_width = sizes[1]
            self.side_panel.hide()
        log.info("Panel de clips %s", "visible" if visible else "oculto")

    @Slot(int)
    def _on_zoom_changed(self, value: int) -> None:
        self._zoom = value / 100.0
        for row in self.rows.rows():
            row.set_zoom(self._zoom)

    # --- Drag & drop ---
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".mp4" and path.is_file():
                paths.append(path)
        if paths:
            self._ingest_paths(paths)

    # --- Ingesta ---
    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir MP4", str(Path.home()), VIDEO_FILTER)
        if files:
            self._ingest_paths([Path(f) for f in files])

    def _ingest_paths(self, paths: list[Path]) -> None:
        if self._busy:
            log.warning("Ignoro ingestión: UI ocupada")
            return
        existing = set(self._media)
        fresh = [p.resolve() for p in paths if p.resolve() not in existing]
        if not fresh:
            log.info("Nada nuevo que añadir")
            return

        log.info("Ingestión de %d clip(s): %s", len(fresh), [p.name for p in fresh])
        self._set_busy(True, "Analizando clips…")
        self.progress.setValue(0)

        self._probe_thread = QThread(self)
        self._probe_worker = ProbeWorker(fresh)
        self._probe_worker.moveToThread(self._probe_thread)

        self._probe_thread.started.connect(self._probe_worker.run)
        self._probe_worker.progress.connect(
            self._on_probe_progress, Qt.ConnectionType.QueuedConnection
        )
        self._probe_worker.finished.connect(
            self._on_probe_ok, Qt.ConnectionType.QueuedConnection
        )
        self._probe_worker.failed.connect(
            self._on_probe_fail, Qt.ConnectionType.QueuedConnection
        )
        self._probe_worker.finished.connect(self._probe_thread.quit)
        self._probe_worker.failed.connect(self._probe_thread.quit)
        self._probe_thread.finished.connect(self._on_probe_thread_finished)
        self._probe_thread.start()

    @Slot(float, str)
    def _on_probe_progress(self, value: float, message: str) -> None:
        self.progress.setValue(int(value * 500))
        self._set_status(message)

    @Slot(list)
    def _on_probe_ok(self, medias: list) -> None:
        log.info("Análisis OK: %d clip(s)", len(medias))
        for media in medias:
            self._media[media.path] = media
        self._start_frames()

    @Slot(str)
    def _on_probe_fail(self, message: str) -> None:
        log.error("Análisis falló: %s", message)
        self._probe_worker = None
        self._set_busy(False, "Error al analizar")
        QMessageBox.warning(self, "Análisis", message)

    @Slot()
    def _on_probe_thread_finished(self) -> None:
        log.info("Hilo de análisis finalizado")
        self._probe_worker = None
        self._probe_thread = None

    # --- Fotogramas ---
    def _start_frames(self) -> None:
        pending = [
            (path, media.duration)
            for path, media in self._media.items()
            if path not in self._frames or isinstance(self._frames[path], Exception)
        ]
        if not pending:
            self._rebuild_groups()
            self._set_busy(False, f"{len(self._media)} clip(s) listos")
            return

        self.progress.setValue(500)
        self._set_status(f"Comparando fotogramas de {len(pending)} clip(s)…")
        self._frames_thread = QThread(self)
        self._frames_worker = FramesWorker(pending)
        self._frames_worker.moveToThread(self._frames_thread)
        self._frames_thread.started.connect(self._frames_worker.run)
        self._frames_worker.progress.connect(
            self._on_frames_progress, Qt.ConnectionType.QueuedConnection
        )
        self._frames_worker.finished.connect(
            self._on_frames_ok, Qt.ConnectionType.QueuedConnection
        )
        self._frames_worker.failed.connect(
            self._on_frames_fail, Qt.ConnectionType.QueuedConnection
        )
        self._frames_worker.finished.connect(self._frames_thread.quit)
        self._frames_worker.failed.connect(self._frames_thread.quit)
        self._frames_thread.finished.connect(self._on_frames_thread_finished)
        self._frames_thread.start()

    @Slot(float, str)
    def _on_frames_progress(self, value: float, message: str) -> None:
        self.progress.setValue(500 + int(value * 500))
        self._set_status(message)

    @Slot(object)
    def _on_frames_ok(self, results: object) -> None:
        if not isinstance(results, dict):
            results = {}
        for path, result in results.items():
            self._frames[path] = result
        failures = [p for p, r in results.items() if isinstance(r, Exception)]
        if failures:
            log.warning("Fotogramas no legibles en %d clip(s)", len(failures))
        self.progress.setValue(1000)
        self._rebuild_groups()
        self._set_busy(False, f"{len(self._media)} clip(s) listos")

    @Slot(str)
    def _on_frames_fail(self, message: str) -> None:
        log.error("Análisis de fotogramas falló: %s", message)
        self.progress.setValue(0)
        self._set_busy(False, "Error al comparar fotogramas")
        QMessageBox.warning(self, "Fotogramas", message)

    @Slot()
    def _on_frames_thread_finished(self) -> None:
        self._frames_worker = None
        self._frames_thread = None

    # --- Agrupado / filas ---
    def _rebuild_groups(self) -> None:
        """
        Recalcula el agrupado y repinta las filas.

        Con el orden automático encendido manda el encadenado por fotogramas;
        apagado, manda el orden manual de `self._order` y los clips nuevos se
        añaden al final. Un flag por llamada no servía: la ingesta de un clip
        nuevo reconstruía con los valores por defecto y revertía el arrastre.
        """
        items = list(self._media.values())
        by_path = {m.path: m for m in items}
        ordered: list[MediaInfo] = []
        if self._order and not self._auto_sort:
            ordered = [by_path[p] for p in self._order if p in by_path]
            placed = {m.path for m in ordered}
            ordered += [m for m in items if m.path not in placed]
        items = ordered or items

        previous = {
            row.group.reference.media.path: row.active.media.path for row in self.rows.rows()
        }
        selected_ref = next(
            (
                row.group.reference.media.path
                for row in self.rows.rows()
                if row.is_selected()
            ),
            None,
        )
        self._groups = group_clips(
            items,
            self._frames,
            self._threshold,
            preserve_input_order=not self._auto_sort,
            detached=self._detached,
        )
        self._render_rows(previous, selected_ref)
        self._apply_follow_filter()

        if not self.output_edit.text() and items:
            self.output_edit.setText(str(items[0].path.with_name("concat_output.mp4")))
        self._refresh_montage()
        self._update_mode_panel()
        self._update_actions()

    def _refresh_montage(self, reveal: bool = False) -> None:
        """Timeline y reproductor: filas visibles con su clip activo, mismo orden."""
        rows = self._visible_rows()
        slots = [
            TimelineSlot(
                row=row.index,
                members=tuple(
                    TimelineMember(member.media, member.frames) for member in row.group.members
                ),
                active=row.active_index,
            )
            for row in rows
        ]
        self.montage.set_slots(slots)
        self.player.set_clips([(slot.media, slot.frames) for slot in slots])
        self._sync_selection_visuals()
        if reveal:
            self._reveal_selection()

    def _sync_selection_visuals(self) -> None:
        """Marca en el timeline el clip de la fila seleccionada y refresca el resumen."""
        chosen = next((row for row in self._visible_rows() if row.is_selected()), None)
        self.montage.set_selected(chosen.active.media.path if chosen else None)
        self._update_selection_detail()

    # --- selección compartida panel ↔ timeline ---
    def _visible_rows(self) -> list[ClipGroupRow]:
        return [row for row in self.rows.rows() if not row.isHidden()]

    def _row_for_path(self, path: Path) -> ClipGroupRow | None:
        """Fila visible que contiene ese clip, sea o no su variante activa."""
        return next(
            (
                row
                for row in self._visible_rows()
                if any(tile.clip.media.path == path for tile in row.tiles)
            ),
            None,
        )

    def _activate_member(self, row: ClipGroupRow, path: Path) -> bool:
        """Pone `path` como clip activo de la fila; dice si hubo cambio."""
        index = next(
            (i for i, tile in enumerate(row.tiles) if tile.clip.media.path == path), None
        )
        if index is None or index == row.active_index:
            return False
        row.set_active_member(index)
        return True

    def _montage_is_stale(self) -> bool:
        """¿El timeline sigue mostrando el clip activo de cada fila visible?"""
        visible = self._visible_rows()
        slots = self.montage.slots()
        if len(slots) != len(visible):
            return True
        return any(
            slot.media.path != row.active.media.path
            for slot, row in zip(slots, visible, strict=True)
        )

    def _select_rows(self, target: ClipGroupRow) -> None:
        """Deja una única fila seleccionada y lleva el panel hasta ella."""
        for row in self.rows.rows():
            row.set_selected(row is target)
        if self._montage_is_stale():
            # El clip activo cambió sin pasar por `_refresh_montage` (p. ej. al
            # pulsar otra variante en el panel): el tramo debe cambiar de clip.
            self._refresh_montage()
        else:
            self._sync_selection_visuals()
        self._update_actions()
        self._reveal_selection()

    def _reveal_selection(self) -> None:
        """Si el panel está a la vista, la fila seleccionada no puede quedar fuera."""
        if self.side_panel.isHidden():
            return
        chosen = next((row for row in self._visible_rows() if row.is_selected()), None)
        if chosen is not None:
            self.scroll.ensureWidgetVisible(chosen, 0, 24)

    def _focus_clip(
        self,
        path: Path,
        *,
        move_cursor: bool = True,
        local: float = 0.0,
    ) -> ClipGroupRow | None:
        """
        Único camino para cambiar de clip: panel, timeline y reproducción.

        Deja el clip seleccionado en las dos vistas: si es una variante no
        activa la activa, marca su fila, la enseña en el panel y, salvo que se
        venga de la propia reproducción, lleva el cursor del timeline a ese
        punto.
        """
        row = self._row_for_path(path)
        if row is None:
            return None
        # Activar la variante deja el timeline desfasado; `_select_rows` lo detecta
        # y reconstruye los tramos con el clip nuevo.
        self._activate_member(row, path)
        self._select_rows(row)
        if move_cursor and self.player.current_path != path:
            self.player.seek_to_clip(path, local)
        return row

    def _render_rows(
        self, previous: dict[Path, Path], selected_ref: Path | None = None
    ) -> None:
        self.rows.clear()
        if not self._groups:
            self.empty_hint.show()
            self.scroll.hide()
            self.rows.hide()
            self._update_list_title()
            return

        self.empty_hint.hide()
        self.scroll.show()
        self.rows.show()
        for index, group in enumerate(self._groups):
            row = ClipGroupRow(group, index)
            row.set_zoom(self._zoom)
            row.selected.connect(self._on_row_selected)
            row.play_requested.connect(self._open_in_system_player)
            row.preview_requested.connect(self._on_preview_requested)
            row.remove_requested.connect(self._on_remove_requested)
            row.detach_requested.connect(self._on_detach_requested)
            row.follow_toggled.connect(self._on_follow_toggled)
            row.hover_changed.connect(self._on_row_hovered)
            for tile in row.tiles:
                tile.set_detach_checked(tile.clip.media.path in self._detached)
            active_path = previous.get(group.reference.media.path)
            if active_path is not None:
                for member_index, member in enumerate(group.members):
                    if member.media.path == active_path:
                        row.set_active_member(member_index)
                        break
            self.rows.add_row(row)

        self._update_list_title()
        if self.rows.rows():
            # Conserva la fila seleccionada tras un recálculo o un arrastre.
            keep = next(
                (
                    index
                    for index, group in enumerate(self._groups)
                    if group.reference.media.path == selected_ref
                ),
                0,
            )
            self._on_row_selected(keep, seek_player=selected_ref is None)

    def _update_list_title(self) -> None:
        rows = len(self._groups)
        clips = len(self._media)
        if not clips:
            self.list_title.setText("CLIPS · VACÍO")
            return
        self.list_title.setText(f"CLIPS · {clips} EN {rows} FILA{'S' if rows != 1 else ''}")

    # --- Filtro «mostrar siguientes» ---
    def _apply_follow_filter(self) -> None:
        """
        Recalcula qué tarjetas sobreviven al filtro del interruptor.

        El filtro es único: solo puede estar encendida la tarjeta ancla. Si el
        ancla desapareció de la lista, el filtro se retira solo.
        """
        kept = None
        if self._follow_anchor is not None:
            kept = follower_paths(self._groups, self._follow_anchor, self._threshold)
            if not kept:
                log.info("Ancla de filtro ausente (%s): se muestra todo", self._follow_anchor)
                self._follow_anchor = None

        rows = self.rows.rows()
        for row in rows:
            row.apply_follow_filter(kept)
            row.sync_follow_switches(self._follow_anchor)
            row.setVisible(kept is None or bool(row.visible_tiles))

        self.btn_show_all.setVisible(self._follow_anchor is not None)
        self._keep_visible_selection(rows)
        self._refresh_montage()

    @Slot(object)
    def _on_row_hovered(self, path: object) -> None:
        """Hover de una fila → su clip se destaca en el montaje completo."""
        if path is None:
            self.montage.set_highlight(None)
            return
        self.montage.set_highlight(path if isinstance(path, Path) else Path(str(path)))

    @Slot(object, float)
    def _on_montage_segment_clicked(self, path: object, local: float) -> None:
        """Pulsar un tramo selecciona ese mismo clip en el panel y mueve el cursor."""
        target = path if isinstance(path, Path) else Path(str(path))
        if self._focus_clip(target, move_cursor=False) is None:
            log.warning("Tramo del montaje sin fila asociada: %s", target)
            return
        log.info("Timeline: cursor en %s (%.2fs)", target.name, local)
        self.player.seek_to_clip(target, local)

    @Slot(int, int)
    def _on_timeline_moved(self, source: int, gap: int) -> None:
        """
        Arrastrar un tramo del timeline reordena su fila.

        El timeline sólo enseña filas visibles, así que los índices se traducen
        al espacio completo que maneja `GroupRowList.reorder`; soltar el clip
        donde ya estaba no cambia nada.
        """
        visible = self._visible_rows()
        total = len(self.rows.rows())
        if not visible or not (0 <= source < len(visible)):
            return
        if gap in (source, source + 1):
            return
        origin = visible[source]
        target = visible[gap].index if gap < len(visible) else total
        log.info("Timeline: fila %d movida al hueco %d", origin.index + 1, gap)
        self.rows.reorder(origin.index, target)
        self._set_status(f"Fila {origin.index + 1} movida en el timeline")

    @Slot(int, int)
    def _on_variant_chosen(self, slot_index: int, member_index: int) -> None:
        """Elegir otra variante en el timeline cambia el clip activo de su fila."""
        visible = self._visible_rows()
        if not (0 <= slot_index < len(visible)):
            return
        row = visible[slot_index]
        if not (0 <= member_index < len(row.tiles)):
            return
        row.set_active_member(member_index)
        chosen = row.active.media
        log.info(
            "Timeline: variante %d de la fila %d (%s)",
            member_index + 1,
            row.index + 1,
            chosen.path.name,
        )
        self._refresh_montage()
        self._select_rows(row)
        self.player.seek_to_clip(chosen.path, 0.0)
        self._set_status(f"Fila {row.index + 1}: {chosen.label}")

    def _keep_visible_selection(self, rows: list[ClipGroupRow]) -> None:
        """La fila seleccionada no puede quedar oculta por el filtro."""
        if not rows:
            return
        selected = next((i for i, row in enumerate(rows) if row.is_selected()), None)
        if selected is not None and not rows[selected].isHidden():
            self._on_row_selected(selected, seek_player=False)
            return
        self._on_row_selected(
            next((i for i, row in enumerate(rows) if not row.isHidden()), 0),
            seek_player=False,
        )

    @Slot(object, bool)
    def _on_follow_toggled(self, media: object, checked: bool) -> None:
        if isinstance(media, MediaInfo):
            anchor = media.path
        else:
            anchor = Path(str(media))
        self._follow_anchor = anchor if checked else None
        log.info("Filtro «mostrar siguientes» → %s", self._follow_anchor or "desactivado")
        self._apply_follow_filter()
        self._set_status(
            f"Mostrando lo que sigue a {anchor.name}" if checked else "Mostrando todos los clips"
        )

    @Slot()
    def _clear_follow_filter(self) -> None:
        if self._follow_anchor is None:
            return
        log.info("Filtro «mostrar siguientes» desactivado desde la cabecera")
        self._follow_anchor = None
        self._apply_follow_filter()
        self._set_status("Mostrando todos los clips")

    @Slot(object, bool)
    def _on_detach_requested(self, path: object, separated: bool) -> None:
        """Saca el clip a una fila propia debajo, o lo devuelve a la suya."""
        if isinstance(path, MediaInfo):
            resolved = path.path
        elif isinstance(path, Path):
            resolved = path
        else:
            resolved = Path(str(path))
        if separated:
            self._detach_clip(resolved)
        else:
            self._reattach_clip(resolved)

    def _detach_clip(self, path: Path) -> None:
        """
        Materializa el orden actual y coloca el clip justo tras sus variantes.

        El agrupado automático fusionaría otra vez el clip en el siguiente
        recálculo, así que la separación se guarda en `_detached` (el agrupado
        deja de juntarlo) y en `_order` (queda inmediatamente debajo de donde
        estaba). Es el mismo precio que paga el arrastre: el orden pasa a ser
        manual y el encadenado por fotogramas deja de reordenar las filas.
        """
        row = next(
            (r for r in self.rows.rows() if any(t.clip.media.path == path for t in r.tiles)),
            None,
        )
        if row is None:
            return
        siblings = [t.clip.media.path for t in row.tiles if t.clip.media.path != path]
        if not siblings:
            self._set_status("Ese clip ya está solo en su fila")
            self._sync_detach_switch(path, False)
            return

        order = [p for p in self._current_order() if p != path]
        position = order.index(siblings[-1]) + 1
        order.insert(position, path)
        self._detached.add(path)
        self._order = order
        changed = self._surrender_auto_sort()

        label = self._media[path].label if path in self._media else path.name
        log.info("Clip separado a fila propia: %s", path)
        self._rebuild_groups()
        prefix = "Orden manual activado · " if changed else ""
        self._set_status(f"{prefix}{label} movido a una fila propia")

    def _reattach_clip(self, path: Path) -> None:
        """
        Devuelve el clip a la fila de sus variantes: el agrupado vuelve a juntarlo.

        No se toca `_order`: con la marca fuera, el agrupado rehace el grupo y la
        posición la decide el orden manual ya fijado.
        """
        if path not in self._detached:
            return
        self._detached.discard(path)
        label = self._media[path].label if path in self._media else path.name
        log.info("Clip devuelto a su fila: %s", path)
        self._rebuild_groups()
        self._set_status(f"{label} devuelto a su fila")

    def _sync_detach_switch(self, path: Path, checked: bool) -> None:
        """Refleja el estado real en el botón ↓ sin reemitir la señal."""
        for row in self.rows.rows():
            for tile in row.tiles:
                if tile.clip.media.path == path:
                    tile.set_detach_checked(checked)
                    return

    def _current_order(self) -> list[Path]:
        """Orden de todos los clips; completa la cola con los que aún no estén en `_order`."""
        if not self._order:
            return [m.media.path for row in self.rows.rows() for m in row.group.members]
        placed = set(self._order)
        return list(self._order) + [p for p in self._media if p not in placed]

    def _on_row_selected(self, index: int, seek_player: bool = True) -> None:
        """
        Fila elegida en el panel: se marca también en el timeline.

        Si `seek_player` está activo, el cursor del timeline salta a ese clip
        (salvo que ya estuviera en él), de forma que las dos vistas hablan
        siempre del mismo clip.
        """
        row = next((r for r in self.rows.rows() if r.index == index), None)
        if row is None:
            return
        self._select_rows(row)
        if not seek_player:
            return
        target = row.active.media.path
        if self.player.current_path != target:
            self.player.seek_to_clip(target, 0.0)

    @Slot(int)
    def _on_player_clip_changed(self, index: int) -> None:
        """La reproducción avanza → ese mismo clip se marca en el panel."""
        visible = self._visible_rows()
        if not (0 <= index < len(visible)):
            return
        row = visible[index]
        if row.is_selected():
            return
        self._select_rows(row)

    @Slot(object)
    def _on_preview_requested(self, media: object) -> None:
        if not isinstance(media, MediaInfo):
            return
        self._focus_clip(media.path, move_cursor=False)
        self.player.preview(media.path)

    @Slot(object)
    def _on_remove_requested(self, media: object) -> None:
        if isinstance(media, MediaInfo):
            self._remove_clip(media.path)

    @Slot()
    def _on_rows_reordered(self) -> None:
        """Fija el orden manual usando TODOS los miembros, no sólo la referencia.

        Con sólo la referencia, arrastrar una fila con variantes borraba del
        modelo los miembros no activos y el clip desaparecía sin aviso.
        """
        changed = self._surrender_auto_sort()
        ordered: list[Path] = []
        for row in self.rows.rows():
            ordered.extend(member.media.path for member in row.group.members)
        self._order = ordered
        self._rebuild_groups()
        if changed:
            self._set_status("Orden manual activado al arrastrar una fila")

    def _selected_media(self) -> list[MediaInfo]:
        """Un clip por fila visible (el activo), en el orden actual de las filas."""
        return [row.active.media for row in self._visible_rows()]

    def _update_selection_detail(self) -> None:
        selected = self._selected_media()
        if not selected:
            self.selection_detail.setText("Sin clips")
            return
        total = sum(m.duration for m in selected)
        groups = len(self._visible_rows())
        self.selection_detail.setText(
            f"{groups} fila(s) · {len(selected)} clip(s) · salida ≈ {format_duration(total)}"
        )

    @Slot(bool)
    def _on_sort_toggled(self, checked: bool) -> None:
        """
        Enciende o apaga el encadenado automático por fotogramas.

        Al apagarlo se congela el orden que se está viendo, para que el
        siguiente recálculo no lo reordene a espaldas del usuario; al
        encenderlo se descarta ese orden manual y manda la continuidad.
        """
        self._auto_sort = checked
        self._sync_sort_switch()
        if checked:
            self._order = []
            log.info("Orden automático encendido: se re-encadena por fotogramas")
            message = "Orden automático: encadenado por fotogramas"
        else:
            self._order = self._current_order()
            log.info("Orden automático apagado: manda el orden manual")
            message = "Orden manual: coloca las filas a tu gusto"
        self._rebuild_groups()
        self._set_status(message)

    def _sync_sort_switch(self) -> None:
        """Refleja `_auto_sort` en el interruptor sin reemitir `toggled`."""
        self.sort_switch.blockSignals(True)
        self.sort_switch.setChecked(self._auto_sort)
        self.sort_switch.blockSignals(False)

    def _surrender_auto_sort(self) -> bool:
        """
        Un gesto manual de montaje apaga el automático sin reordenar nada.

        Devolver si estaba encendido permite al llamante explicarlo en su
        propio mensaje de estado, en vez de pisarse dos avisos.
        """
        if not self._auto_sort:
            return False
        self._auto_sort = False
        self._sync_sort_switch()
        log.info("Orden automático apagado por un gesto manual")
        return True

    def _on_threshold_changed(self, index: int) -> None:
        self._threshold = float(self.threshold_box.itemData(index))
        log.info("Umbral de agrupado → %.2f", self._threshold)
        if self._media:
            self._rebuild_groups()

    # --- Acciones sobre clips ---
    def _remove_selection(self) -> None:
        current = next((row for row in self.rows.rows() if row.is_selected()), None)
        if current is None or not self._groups:
            return
        self._remove_clip(current.active.media.path)

    def _remove_clip(self, path: Path) -> None:
        group = next(
            (g for g in self._groups if any(m.media.path == path for m in g.members)),
            None,
        )
        target = (
            next((m for m in group.members if m.media.path == path), None)
            if group is not None
            else None
        )
        if target is None:
            return
        remaining = [m for m in group.members if m is not target]
        del self._media[path]
        self._frames.pop(path, None)
        self._detached.discard(path)
        self._order = [p for p in self._order if p != path]
        log.info("Quitado %s (quedan %d en su fila)", target.media.label, len(remaining))
        self._rebuild_groups()
        self._set_status(f"Quitado {target.media.label}")

    def _clear_all(self) -> None:
        self._media.clear()
        self._frames.clear()
        self._groups = []
        self._order = []
        self._detached.clear()
        self._follow_anchor = None
        self.btn_show_all.setVisible(False)
        self.rows.clear()
        self.empty_hint.show()
        self.scroll.hide()
        self.rows.hide()
        self.montage.set_clips([])
        self.player.set_clips([])
        self._update_list_title()
        self._update_mode_panel()
        self._update_actions()
        self._set_status("Lista vacía")

    def _open_in_system_player(self, media: object) -> None:
        if not isinstance(media, MediaInfo):
            return
        url = QUrl.fromLocalFile(str(media.path))
        if not QDesktopServices.openUrl(url):
            log.warning("No se pudo abrir %s con el reproductor del sistema", media.path)
            self._set_status(f"No se pudo abrir {media.label}")
            return
        log.info("Reproducción externa: %s", media.path)
        self._set_status(f"Abriendo {media.label} en el reproductor del sistema")

    # --- Modo / acciones ---
    def _update_mode_panel(self) -> None:
        selected = self._selected_media()
        if len(selected) < 2:
            self.mode_pill.setText("Esperando clips")
            self.mode_pill.setStyleSheet(
                f"background-color: {COLORS['surface_raised']}; color: {COLORS['ink_muted']};"
                f"border: 1px solid {COLORS['line']};"
            )
            self.mode_detail.setText("Añade al menos dos MP4 para decidir el modo.")
            self.mode_detail.setToolTip(self.mode_detail.full_text())
            self._update_selection_detail()
            return

        self._report = analyze_compatibility(selected)
        pipeline = select_pipeline(self._report, self._gpu)
        if pipeline is Pipeline.COPY:
            self.mode_pill.setText("COPY · sin recodificar")
            self.mode_pill.setStyleSheet(
                f"background-color: {COLORS['ok_soft']}; color: {COLORS['ok']};"
            )
            self.mode_detail.setText(
                "Clips compatibles: se remultiplexan sin pérdida de calidad ni GPU."
            )
            self.mode_detail.setToolTip(self.mode_detail.full_text())
        else:
            labels = {
                Pipeline.CUDA_FULL: "RECODIFICAR · CUDA full",
                Pipeline.NVENC: "RECODIFICAR · NVENC",
                Pipeline.CPU: "RECODIFICAR · CPU",
            }
            self.mode_pill.setText(labels[pipeline])
            self.mode_pill.setStyleSheet(
                f"background-color: {COLORS['warn_soft']}; color: {COLORS['warn']};"
            )
            reasons = self._report.reasons[:4]
            summary = " · ".join(reasons) if reasons else "Se normalizará la salida."
            self.mode_detail.setText(f"Diferencias: {summary}")
            self.mode_detail.setToolTip("\n".join(f"• {r}" for r in self._report.reasons))
        self._update_selection_detail()

    def _update_actions(self) -> None:
        selected = self._selected_media()
        can_run = (
            len(selected) >= 2 and bool(self.output_edit.text().strip()) and not self._busy
        )
        self.btn_run.setEnabled(can_run or self._concatenating)
        for widget in (self.btn_add, self.btn_clear, self.threshold_box):
            widget.setEnabled(not self._busy)
        self.sort_switch.setEnabled(not self._busy)
        self.btn_clear.setEnabled(not self._busy and bool(self._media))
        self.btn_show_all.setEnabled(not self._busy)
        self._sync_run_button()

    def _sync_run_button(self) -> None:
        """El botón principal es también el freno durante una concatenación."""
        if self._concatenating:
            self.btn_run.setText("Detener")
            self.btn_run.setObjectName("Danger")
        else:
            self.btn_run.setText("Concatenar")
            self.btn_run.setObjectName("Primary")
        self.btn_run.style().unpolish(self.btn_run)
        self.btn_run.style().polish(self.btn_run)

    def _on_run_clicked(self) -> None:
        if self._concatenating:
            self._cancel_concat()
            return
        self._start_concat()

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar resultado", self.output_edit.text() or "concat_output.mp4", VIDEO_FILTER
        )
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.output_edit.setText(path)
            self._update_actions()

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self._set_status(status)
        self._update_actions()

    # --- Concatenación ---
    def _start_concat(self) -> None:
        items = self._selected_media()
        if len(items) < 2:
            return
        out = Path(self.output_edit.text().strip())
        if not out.name:
            QMessageBox.warning(self, "Salida", "Indica un fichero de salida.")
            return
        if out.exists():
            reply = QMessageBox.question(
                self,
                "Sobrescribir",
                f"{out.name} ya existe. ¿Sobrescribir?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        report = analyze_compatibility(items)
        self._report = report
        pipeline = select_pipeline(report, self._gpu)
        log.info(
            "Concatenar %d clips (%d filas) → %s (pipeline=%s)",
            len(items),
            len(self.rows.rows()),
            out,
            pipeline,
        )
        self._concatenating = True
        self._set_busy(True, "Concatenando…")
        self.progress.setValue(0)

        self._concat_thread = QThread(self)
        self._concat_worker = ConcatWorker(items, out, report, self._gpu)
        self._concat_worker.moveToThread(self._concat_thread)
        self._concat_thread.started.connect(self._concat_worker.run)
        self._concat_worker.progress.connect(
            self._on_progress, Qt.ConnectionType.QueuedConnection
        )
        self._concat_worker.finished.connect(
            self._on_concat_ok, Qt.ConnectionType.QueuedConnection
        )
        self._concat_worker.failed.connect(
            self._on_concat_fail, Qt.ConnectionType.QueuedConnection
        )
        self._concat_worker.finished.connect(self._concat_thread.quit)
        self._concat_worker.failed.connect(self._concat_thread.quit)
        self._concat_thread.finished.connect(self._on_concat_thread_finished)
        self._concat_thread.start()

    @Slot(float, str)
    def _on_progress(self, value: float, message: str) -> None:
        self.progress.setValue(int(value * 1000))
        self._set_status(message)

    @Slot(object)
    def _on_concat_ok(self, result: object) -> None:
        assert isinstance(result, ConcatResult)
        log.info("Concat OK: %s", result.output)
        self._concatenating = False
        self.progress.setValue(1000)
        self._set_busy(False, f"Listo · {result.used_encoder} → {result.output.name}")
        QMessageBox.information(
            self,
            "Completado",
            f"Guardado en:\n{result.output}\n\n"
            f"Pipeline: {result.pipeline}\nEncoder: {result.used_encoder}",
        )

    @Slot(str)
    def _on_concat_fail(self, message: str) -> None:
        log.error("Concat falló: %s", message)
        self._concatenating = False
        self._set_busy(False, "Error")
        QMessageBox.critical(self, "Concatenación", message)

    @Slot()
    def _on_concat_thread_finished(self) -> None:
        log.info("Hilo de concatenación finalizado")
        self._concat_worker = None
        self._concat_thread = None

    def _cancel_concat(self) -> None:
        if self._concat_worker:
            self._concat_worker.cancel()
            self._set_status("Cancelando…")
            self.btn_run.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        """
        Cierra cancelando de verdad: nunca `terminate()` sobre un hilo vivo.

        `quit()` no interrumpe un worker bloqueado en `subprocess.run`, así que
        para el hilo de fotogramas se pide cancelación cooperativa (entre
        clips) y se espera. Matar el hilo dejaría ffmpeg huérfanos y podría
        abortar el proceso al destruir el `QThread`.
        """
        log.info("Cerrando ventana")
        if self._concat_worker:
            self._concat_worker.cancel()
        if self._frames_worker is not None:
            self._frames_worker.cancel()
        self.player.stop()

        for thread in (self._probe_thread, self._frames_thread, self._concat_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                if not thread.wait(CLOSE_WAIT_MS):
                    log.warning(
                        "Hilo aún ocupado al cerrar; se espera sin terminar el proceso"
                    )
                    thread.wait(CLOSE_WAIT_MS)
        event.accept()
