"""Ventana principal."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from video_concat.core.compatibility import CompatibilityReport, analyze_compatibility
from video_concat.core.concat import ConcatError, ConcatJob, ConcatResult, Pipeline, select_pipeline
from video_concat.core.gpu import GpuCapabilities, detect_gpu
from video_concat.core.probe import MediaInfo, ProbeError, probe_file
from video_concat.logutil import get_logger
from video_concat.ui.styles import APP_STYLE, COLORS
from video_concat.ui.widgets import TimelineStrip

VIDEO_FILTER = "Vídeos MP4 (*.mp4 *.MP4)"
log = get_logger("ui")


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


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
        self.resize(980, 680)
        self.setMinimumSize(820, 560)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)

        self._items: list[MediaInfo] = []
        self._report: CompatibilityReport | None = None
        self._gpu = detect_gpu()
        self._probe_thread: QThread | None = None
        self._probe_worker: ProbeWorker | None = None
        self._concat_thread: QThread | None = None
        self._concat_worker: ConcatWorker | None = None
        self._busy = False

        log.info("GPU detectada: %s (cuda_full=%s)", self._gpu, self._gpu.cuda_full)

        self._build_ui()
        self._update_mode_panel()
        self._update_actions()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        header = QHBoxLayout()
        brand_col = QVBoxLayout()
        brand = QLabel("VIDEO CONCAT")
        brand.setObjectName("Brand")
        tagline = QLabel("Montaje MP4 · copy inteligente · CUDA full cuando hace falta")
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

        # Timeline firma
        timeline_frame = QFrame()
        timeline_frame.setObjectName("TimelineFrame")
        tf_layout = QVBoxLayout(timeline_frame)
        tf_layout.setContentsMargins(12, 10, 12, 8)
        title = QLabel("LÍNEA DE TIEMPO")
        title.setObjectName("SectionTitle")
        self.timeline = TimelineStrip()
        tf_layout.addWidget(title)
        tf_layout.addWidget(self.timeline)
        layout.addWidget(timeline_frame)

        # Lista + botones
        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        list_title = QLabel("CLIPS")
        list_title.setObjectName("SectionTitle")
        left.addWidget(list_title)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.model().rowsMoved.connect(self._on_rows_reordered)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left.addWidget(self.list_widget, stretch=1)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Añadir…")
        self.btn_add.clicked.connect(self._add_files)
        self.btn_remove = QPushButton("Quitar")
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_up = QPushButton("↑")
        self.btn_up.setFixedWidth(40)
        self.btn_up.clicked.connect(lambda: self._move_selection(-1))
        self.btn_down = QPushButton("↓")
        self.btn_down.setFixedWidth(40)
        self.btn_down.clicked.connect(lambda: self._move_selection(1))
        self.btn_clear = QPushButton("Vaciar")
        self.btn_clear.clicked.connect(self._clear_all)
        for b in (self.btn_add, self.btn_remove, self.btn_up, self.btn_down, self.btn_clear):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        left.addLayout(btn_row)
        body.addLayout(left, stretch=3)

        # Panel modo
        right = QFrame()
        right.setObjectName("Panel")
        right.setFixedWidth(280)
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

        # Progreso + acciones
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

    # --- Lista ---
    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir MP4", str(Path.home()), VIDEO_FILTER)
        if files:
            self._ingest_paths([Path(f) for f in files])

    def _ingest_paths(self, paths: list[Path]) -> None:
        if self._busy:
            log.warning("Ignoro ingestión: UI ocupada")
            return
        existing = {m.path for m in self._items}
        fresh = [p.resolve() for p in paths if p.resolve() not in existing]
        if not fresh:
            log.info("Nada nuevo que añadir")
            return

        log.info("Ingestión de %d clip(s): %s", len(fresh), [p.name for p in fresh])
        self._set_busy(True, "Analizando clips…")
        self.progress.setValue(0)

        # Referencias fuertes: si el worker es local, Python lo GC y aborta el QThread.
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
        log.info("Hilo de análisis arrancado")

    @Slot(float, str)
    def _on_probe_progress(self, value: float, message: str) -> None:
        self.progress.setValue(int(value * 1000))
        self.status_label.setText(message)

    @Slot(list)
    def _on_probe_ok(self, medias: list) -> None:
        log.info("Análisis OK: %d clip(s)", len(medias))
        self._items.extend(medias)
        self._refresh_list()
        self.progress.setValue(1000)
        self._set_busy(False, f"Añadidos {len(medias)} clip(s)")
        if not self.output_edit.text() and self._items:
            default = self._items[0].path.with_name("concat_output.mp4")
            self.output_edit.setText(str(default))

    @Slot(str)
    def _on_probe_fail(self, message: str) -> None:
        log.error("Análisis falló: %s", message)
        self._set_busy(False, "Error al analizar")
        QMessageBox.warning(self, "Análisis", message)

    @Slot()
    def _on_probe_thread_finished(self) -> None:
        log.info("Hilo de análisis finalizado")
        self._probe_worker = None
        self._probe_thread = None

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for media in self._items:
            audio = (
                f"{media.audio.codec_name} {media.audio.sample_rate} Hz"
                if media.audio
                else "sin audio"
            )
            text = (
                f"{media.label}\n"
                f"{media.resolution} · {media.fps_label} fps · "
                f"{media.video.codec_name if media.video else '?'} · "
                f"{_format_duration(media.duration)} · {_format_size(media.size_bytes)} · {audio}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, str(media.path))
            self.list_widget.addItem(item)
        self.timeline.set_items(self._items)
        self._report = analyze_compatibility(self._items) if self._items else None
        self._update_mode_panel()
        self._update_actions()

    def _sync_order_from_list(self) -> None:
        by_path = {str(m.path): m for m in self._items}
        ordered: list[MediaInfo] = []
        for i in range(self.list_widget.count()):
            key = self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            if key in by_path:
                ordered.append(by_path[key])
        self._items = ordered
        self.timeline.set_items(self._items)
        self._report = analyze_compatibility(self._items) if self._items else None
        self._update_mode_panel()

    @Slot()
    def _on_rows_reordered(self) -> None:
        self._sync_order_from_list()

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.list_widget.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._items):
                self._items.pop(row)
        self._refresh_list()

    def _clear_all(self) -> None:
        self._items.clear()
        self._refresh_list()
        self.status_label.setText("Lista vacía")

    def _move_selection(self, delta: int) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if not (0 <= new_row < len(self._items)):
            return
        self._items[row], self._items[new_row] = self._items[new_row], self._items[row]
        self._refresh_list()
        self.list_widget.setCurrentRow(new_row)

    def _update_mode_panel(self) -> None:
        if len(self._items) < 2:
            self.mode_pill.setText("Esperando clips")
            self.mode_pill.setStyleSheet(
                f"background-color: {COLORS['surface_raised']}; color: {COLORS['ink_muted']};"
                f"border: 1px solid {COLORS['line']};"
            )
            self.mode_detail.setText("Añade al menos dos MP4 para decidir el modo.")
            return

        assert self._report is not None
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
            reasons = "\n".join(f"• {r}" for r in self._report.reasons[:6])
            extra = "" if len(self._report.reasons) <= 6 else "\n• …"
            note = (
                "\n\nNota: pad/fps/concat van en CPU; ffmpeg no concatena frames CUDA."
                if pipeline is Pipeline.CUDA_FULL
                else ""
            )
            self.mode_detail.setText(
                f"Hay diferencias entre clips. Se normalizará y recodificará.\n"
                f"{reasons}{extra}{note}"
            )

    def _update_actions(self) -> None:
        can_run = len(self._items) >= 2 and bool(self.output_edit.text().strip()) and not self._busy
        self.btn_run.setEnabled(can_run)
        for b in (self.btn_add, self.btn_remove, self.btn_up, self.btn_down, self.btn_clear):
            b.setEnabled(not self._busy)

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

    def _start_concat(self) -> None:
        if len(self._items) < 2:
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

        report = analyze_compatibility(self._items)
        self._report = report
        self._update_mode_panel()
        pipeline = select_pipeline(report, self._gpu)
        log.info(
            "Concatenar %d clips → %s (pipeline=%s)",
            len(self._items),
            out,
            pipeline,
        )
        self._set_busy(True, "Concatenando…")
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)

        self._concat_thread = QThread(self)
        self._concat_worker = ConcatWorker(self._items, out, report, self._gpu)
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
        if self._concat_worker:
            self._concat_worker.cancel()
        for thread in (self._probe_thread, self._concat_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                if not thread.wait(5000):
                    log.warning("Hilo no terminó a tiempo; se fuerza terminación")
                    thread.terminate()
                    thread.wait(1000)
        event.accept()
