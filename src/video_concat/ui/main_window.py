"""Ventana principal."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence
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
    QVBoxLayout,
    QWidget,
)

from video_concat.core.compatibility import CompatibilityReport, analyze_compatibility
from video_concat.core.concat import ConcatError, ConcatJob, ConcatResult, Pipeline, select_pipeline
from video_concat.core.frames import DEFAULT_THRESHOLD, EdgeFrames, extract_many
from video_concat.core.gpu import GpuCapabilities, detect_gpu
from video_concat.core.grouping import ClipGroup, group_clips
from video_concat.core.probe import MediaInfo, ProbeError, probe_file
from video_concat.logutil import get_logger
from video_concat.ui.formatting import format_duration
from video_concat.ui.styles import APP_STYLE, COLORS
from video_concat.ui.widgets import (
    ClipGroupRow,
    GroupRowList,
    InlinePlayback,
    TimelineStrip,
)

VIDEO_FILTER = "Vídeos MP4 (*.mp4 *.MP4)"
THRESHOLDS = (
    ("0.97 · solo idénticos", 0.97),
    ("0.93 · recomendado", 0.93),
    ("0.88 · permisivo", 0.88),
)
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

    def __init__(self, entries: list[tuple[Path, float]]) -> None:
        super().__init__()
        self.entries = entries

    @Slot()
    def run(self) -> None:
        log.info("FramesWorker arranca (%d clips)", len(self.entries))
        self.progress.emit(0.0, "Comparando primer y último fotograma…")
        try:
            results = extract_many(self.entries)
            log.info("FramesWorker OK (%d clips)", len(results))
            self.finished.emit(results)
        except Exception:  # noqa: BLE001
            log.exception("FramesWorker error inesperado")
            self.finished.emit({})


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
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)

        self._media: dict[Path, MediaInfo] = {}
        self._frames: dict[Path, EdgeFrames | Exception] = {}
        self._groups: list[ClipGroup] = []
        self._selection: list[int] = []
        self._threshold = DEFAULT_THRESHOLD
        self._report: CompatibilityReport | None = None
        self._gpu = detect_gpu()
        self._probe_thread: QThread | None = None
        self._probe_worker: ProbeWorker | None = None
        self._frames_thread: QThread | None = None
        self._frames_worker: FramesWorker | None = None
        self._concat_thread: QThread | None = None
        self._concat_worker: ConcatWorker | None = None
        self._playback: InlinePlayback | None = None
        self._busy = False
        log.info("GPU detectada: %s (cuda_full=%s)", self._gpu, self._gpu.cuda_full)

        self._build_ui()
        self._update_mode_panel()
        self._update_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        brand_col = QVBoxLayout()
        brand = QLabel("VIDEO CONCAT")
        brand.setObjectName("Brand")
        tagline = QLabel("Montaje por fotogramas · un clip por fila · copy inteligente")
        tagline.setObjectName("Tagline")
        brand_col.addWidget(brand)
        brand_col.addWidget(tagline)
        header.addLayout(brand_col, stretch=1)

        self.gpu_label = QLabel()
        self.gpu_label.setObjectName("StatusPill")
        self.gpu_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.gpu_label, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        self._paint_gpu_pill()

        timeline_frame = QFrame()
        timeline_frame.setObjectName("TimelineFrame")
        tf_layout = QVBoxLayout(timeline_frame)
        tf_layout.setContentsMargins(12, 6, 12, 6)
        tf_layout.setSpacing(2)
        title = QLabel("LÍNEA DE TIEMPO")
        title.setObjectName("SectionTitle")
        self.timeline = TimelineStrip()
        tf_layout.addWidget(title)
        tf_layout.addWidget(self.timeline)
        layout.addWidget(timeline_frame)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        head = QHBoxLayout()
        list_title = QLabel("FILAS · PRIMER Y ÚLTIMO FOTOGRAMA")
        list_title.setObjectName("SectionTitle")
        head.addWidget(list_title)
        head.addStretch(1)
        self.threshold_box = QComboBox()
        for label, value in THRESHOLDS:
            self.threshold_box.addItem(label, value)
        self.threshold_box.setCurrentIndex(1)
        self.threshold_box.setFixedWidth(190)
        self.threshold_box.currentIndexChanged.connect(self._on_threshold_changed)
        head.addWidget(self.threshold_box)
        left.addLayout(head)

        self.rows = GroupRowList()
        self.rows.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.rows.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.rows.rows_reordered.connect(self._on_rows_reordered)
        self.rows.hide()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.rows)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left.addWidget(self.scroll, stretch=1)

        self.empty_hint = QLabel(
            "Arrastra MP4 aquí o usa «Añadir…».\n\n"
            "Cada fila agrupa los clips que empiezan y acaban con el mismo fotograma.\n"
            "Haz clic en una fila para elegirla: solo se concatenará una versión por fila."
        )
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet(f"color: {COLORS['ink_muted']};")
        self.empty_hint.setWordWrap(True)
        left.addWidget(self.empty_hint, stretch=3)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Añadir…")
        self.btn_add.clicked.connect(self._add_files)
        self.btn_remove = QPushButton("Quitar fila")
        self.btn_remove.clicked.connect(self._remove_selection)
        self.btn_clear = QPushButton("Vaciar")
        self.btn_clear.clicked.connect(self._clear_all)
        for b in (self.btn_add, self.btn_remove, self.btn_clear):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        left.addLayout(btn_row)
        body.addLayout(left, stretch=1)

        right = QFrame()
        right.setObjectName("Panel")
        right.setFixedWidth(288)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 16, 16, 16)
        rl.setSpacing(10)
        mode_title = QLabel("ESTRATEGIA")
        mode_title.setObjectName("SectionTitle")
        rl.addWidget(mode_title)

        self.mode_pill = QLabel("—")
        self.mode_pill.setObjectName("StatusPill")
        self.mode_pill.setWordWrap(True)
        rl.addWidget(self.mode_pill)

        self.mode_detail = QLabel("Añade al menos dos MP4.")
        self.mode_detail.setWordWrap(True)
        self.mode_detail.setStyleSheet(f"color: {COLORS['ink_muted']};")
        rl.addWidget(self.mode_detail)

        self.selection_detail = QLabel("Sin selección.")
        self.selection_detail.setWordWrap(True)
        self.selection_detail.setStyleSheet(f"color: {COLORS['ink_muted']};")
        rl.addWidget(self.selection_detail)
        rl.addStretch(1)

        out_title = QLabel("SALIDA")
        out_title.setObjectName("SectionTitle")
        rl.addWidget(out_title)
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("ruta/del/resultado.mp4")
        self.output_edit.textChanged.connect(lambda _t: self._update_actions())
        rl.addWidget(self.output_edit)
        self.btn_browse_out = QPushButton("Elegir destino…")
        self.btn_browse_out.clicked.connect(self._browse_output)
        rl.addWidget(self.btn_browse_out)

        body.addWidget(right)
        layout.addLayout(body, stretch=1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        layout.addWidget(self.progress)

        self.status_label = QLabel("Listo")
        self.status_label.setStyleSheet(f"color: {COLORS['ink_muted']};")
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setObjectName("Danger")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_concat)
        self.btn_run = QPushButton("Concatenar")
        self.btn_run.setObjectName("Primary")
        self.btn_run.clicked.connect(self._start_concat)
        actions.addWidget(self.btn_cancel)
        actions.addStretch(1)
        actions.addWidget(self.btn_run)
        layout.addLayout(actions)

        open_act = QAction("Abrir…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._add_files)
        self.addAction(open_act)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

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
        self.status_label.setText(message)

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
            return

        self.progress.setValue(500)
        self.status_label.setText(f"Comparando fotogramas de {len(pending)} clip(s)…")
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
        self._frames_worker.finished.connect(self._frames_thread.quit)
        self._frames_thread.finished.connect(self._on_frames_thread_finished)
        self._frames_thread.start()

    @Slot(float, str)
    def _on_frames_progress(self, value: float, message: str) -> None:
        self.progress.setValue(500 + int(value * 500))
        self.status_label.setText(message)

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

    @Slot()
    def _on_frames_thread_finished(self) -> None:
        self._frames_worker = None
        self._frames_thread = None

    # --- Agrupado / filas ---
    def _rebuild_groups(
        self, ordered_paths: list[Path] | None = None, keep_order: bool = False
    ) -> None:
        items = list(self._media.values())
        by_path = {m.path: m for m in items}
        if ordered_paths is not None:
            items = [by_path[p] for p in ordered_paths if p in by_path]
        elif keep_order:
            current = [row.group.reference.media.path for row in self.rows.rows()]
            if current:
                ordered = [by_path[p] for p in current if p in by_path]
                ordered += [m for m in items if m.path not in set(current)]
                items = ordered

        previous = {
            row.group.reference.media.path: row.active.media.path for row in self.rows.rows()
        }
        self._groups = group_clips(
            items,
            self._frames,
            self._threshold,
            preserve_input_order=keep_order or ordered_paths is not None,
        )
        self._render_rows(previous)

        if not self.output_edit.text() and items:
            self.output_edit.setText(str(items[0].path.with_name("concat_output.mp4")))
        self.timeline.set_items(self._selected_media())
        self._update_mode_panel()
        self._update_actions()

    def _render_rows(self, previous: dict[Path, Path]) -> None:
        self.rows.clear()
        self._selection = []
        if not self._groups:
            self.empty_hint.show()
            self.rows.hide()
            return

        self.empty_hint.hide()
        self.rows.show()
        for index, group in enumerate(self._groups):
            row = ClipGroupRow(group, index)
            row.selected.connect(self._on_row_selected)
            row.player.play_requested.connect(self._toggle_playback)
            active_path = previous.get(group.reference.media.path)
            if active_path is not None:
                for member_index, member in enumerate(group.members):
                    if member.media.path == active_path:
                        row.set_active_member(member_index)
                        break
            self.rows.add_row(row)
            self._selection.append(0)

        if self._selection:
            self._on_row_selected(0)

    @Slot(int)
    def _on_row_selected(self, index: int) -> None:
        if not (0 <= index < len(self._selection)):
            return
        for row in self.rows.rows():
            row.set_selected(row.index == index)
            if row.index == index:
                self._selection[index] = row.active_index
        self.timeline.set_items(self._selected_media())
        self._update_selection_detail()
        self._update_actions()

    @Slot()
    def _on_rows_reordered(self) -> None:
        order = [row.group.reference.media.path for row in self.rows.rows()]
        by_path = {m.path: m for m in self._media.values()}
        self._media = {p: by_path[p] for p in order if p in by_path}
        self._rebuild_groups(keep_order=True)

    def _selected_media(self) -> list[MediaInfo]:
        """Un clip por fila, en el orden actual de las filas."""
        selected: list[MediaInfo] = []
        for row in self.rows.rows():
            member = row.group.resolve(self._selection[row.index])
            selected.append(member.media)
        return selected

    def _update_selection_detail(self) -> None:
        selected = self._selected_media()
        if not selected:
            self.selection_detail.setText("Sin selección.")
            return
        total = sum(m.duration for m in selected)
        groups = len(self.rows.rows())
        self.selection_detail.setText(
            f"{groups} fila(s) · {len(selected)} clip(s) seleccionado(s)\n"
            f"Duración de salida ≈ {format_duration(total)}"
        )

    def _on_threshold_changed(self, index: int) -> None:
        self._threshold = float(self.threshold_box.itemData(index))
        log.info("Umbral de agrupado → %.2f", self._threshold)
        if self._media:
            self._rebuild_groups(keep_order=True)

    # --- Acciones sobre clips ---
    def _remove_selection(self) -> None:
        current = next((i for i, row in enumerate(self.rows.rows()) if row.is_selected()), None)
        if current is None or not self._groups:
            return
        group = self._groups[current]
        active = group.resolve(self._selection[current] if current < len(self._selection) else 0)
        remaining = [m for m in group.members if m is not active]
        del self._media[active.media.path]

        if not remaining:
            self._frames.pop(active.media.path, None)
        log.info("Quitado %s (quedan %d en su fila)", active.media.label, len(remaining))
        self._rebuild_groups(keep_order=True)
        self.status_label.setText(f"Quitado {active.media.label}")

    def _clear_all(self) -> None:
        self._media.clear()
        self._frames.clear()
        self._groups = []
        self._selection = []
        self.rows.clear()
        self.empty_hint.show()
        self.rows.hide()
        self.timeline.set_items([])
        self._update_mode_panel()
        self._update_actions()
        self.status_label.setText("Lista vacía")

    def _toggle_playback(self, media: object) -> None:
        if not isinstance(media, MediaInfo):
            return
        slot = next(
            (row.player for row in self.rows.rows() if row.active.media.path == media.path),
            None,
        )
        if slot is None:
            return
        if self._playback is None:
            self._playback = InlinePlayback(self)
        self._playback.toggle(media, slot)
        self.status_label.setText(
            f"Reproduciendo {media.label}" if self._playback.current else "Listo"
        )

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
            self._update_selection_detail()
            return

        self._report = analyze_compatibility(selected)
        pipeline = select_pipeline(self._report, self._gpu)
        if pipeline is Pipeline.COPY:
            self.mode_pill.setText("Modo COPY · sin recodificar")
            self.mode_pill.setStyleSheet(
                f"background-color: {COLORS['ok_soft']}; color: {COLORS['ok']};"
            )
            self.mode_detail.setText(
                "Los clips son compatibles. Se remultiplexan a velocidad de disco, "
                "sin pérdida de calidad ni uso de GPU."
            )
        else:
            labels = {
                Pipeline.CUDA_FULL: "REENCODE · CUDA full (NVDEC+scale+NVENC)",
                Pipeline.NVENC: "REENCODE · NVENC (decode CPU)",
                Pipeline.CPU: "REENCODE · CPU (libx264)",
            }
            self.mode_pill.setText(labels[pipeline])
            self.mode_pill.setStyleSheet(
                f"background-color: {COLORS['warn_soft']}; color: {COLORS['warn']};"
            )
            reasons = "\n".join(f"• {r}" for r in self._report.reasons[:5])
            extra = "" if len(self._report.reasons) <= 5 else "\n• …"
            note = (
                "\n\nNota: pad/fps/concat van en CPU; ffmpeg no concatena frames CUDA."
                if pipeline is Pipeline.CUDA_FULL
                else ""
            )
            self.mode_detail.setText(
                f"Hay diferencias entre clips. Se normalizará y recodificará.\n"
                f"{reasons}{extra}{note}"
            )
        self._update_selection_detail()

    def _update_actions(self) -> None:
        selected = self._selected_media()
        can_run = (
            len(selected) >= 2 and bool(self.output_edit.text().strip()) and not self._busy
        )
        self.btn_run.setEnabled(can_run)
        has_rows = bool(self.rows.rows())
        for b in (self.btn_add, self.btn_remove, self.btn_clear, self.threshold_box):
            b.setEnabled(not self._busy)
        self.btn_remove.setEnabled(not self._busy and has_rows)
        self.btn_clear.setEnabled(not self._busy and bool(self._media))

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
        self.status_label.setText(status)
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
        self._set_busy(True, "Concatenando…")
        self.btn_cancel.setEnabled(True)
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
        self.status_label.setText(message)

    @Slot(object)
    def _on_concat_ok(self, result: object) -> None:
        assert isinstance(result, ConcatResult)
        log.info("Concat OK: %s", result.output)
        self.btn_cancel.setEnabled(False)
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
        self.btn_cancel.setEnabled(False)
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
            self.status_label.setText("Cancelando…")

    def closeEvent(self, event) -> None:  # noqa: N802
        log.info("Cerrando ventana")
        if self._playback is not None:
            self._playback.stop()
        if self._concat_worker:
            self._concat_worker.cancel()
        for thread in (self._probe_thread, self._frames_thread, self._concat_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                if not thread.wait(5000):
                    log.warning("Hilo no terminó a tiempo; se fuerza terminación")
                    thread.terminate()
                    thread.wait(1000)
        event.accept()
