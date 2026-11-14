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
from video_concat.ui.styles import APP_STYLE, COLORS
from video_concat.ui.widgets import (
    ClipGroupRow,
    GroupRowList,
    MontageStrip,
)

VIDEO_FILTER = "Vídeos MP4 (*.mp4 *.MP4)"
# Espera máxima por hilo al cerrar; nunca se mata el hilo (dejaría ffmpeg huérfanos).
CLOSE_WAIT_MS = 15000
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
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        self.setAcceptDrops(True)
        self.setStyleSheet(APP_STYLE)

        self._media: dict[Path, MediaInfo] = {}
        self._frames: dict[Path, EdgeFrames | Exception] = {}
        self._groups: list[ClipGroup] = []
        self._threshold = DEFAULT_THRESHOLD
        self._order: list[Path] = []
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
        tagline = QLabel("Montaje por fotogramas · todos los clips de una fila a la vista")
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
        title = QLabel("MONTAJE · PRIMER Y ÚLTIMO FOTOGRAMA")
        title.setObjectName("SectionTitle")
        self.montage = MontageStrip()
        self.montage.setToolTip(
            "Montaje completo en orden de concatenación; pasa por una fila para resaltar su clip"
        )
        tf_layout.addWidget(title)
        tf_layout.addWidget(self.montage)
        layout.addWidget(timeline_frame)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        head = QHBoxLayout()
        list_title = QLabel("FILAS · PRIMER Y ÚLTIMO FOTOGRAMA")
        list_title.setObjectName("SectionTitle")
        head.addWidget(list_title)
        head.addStretch(1)
        self.btn_show_all = QPushButton("Mostrar todo")
        self.btn_show_all.setObjectName("Variant")
        self.btn_show_all.setToolTip("Quitar el filtro «mostrar siguientes»")
        self.btn_show_all.setVisible(False)
        self.btn_show_all.clicked.connect(self._clear_follow_filter)
        head.addWidget(self.btn_show_all)
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
            "Cada fila agrupa los clips que empiezan y acaban con el mismo fotograma;\n"
            "se ven todos en línea con su primer y último fotograma.\n"
            "El interruptor de cada tarjeta deja a la vista solo los clips que siguen al suyo;\n"
            "pasa por una fila para destacar su clip en el montaje de arriba."
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
            self._set_busy(False, f"{len(self._media)} clip(s) listos")
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

        El orden manual del usuario vive en `self._order` y manda siempre que
        exista: un flag por llamada no servía, porque la ingesta de un clip
        nuevo reconstruía con los valores por defecto y revertía el arrastre.
        """
        items = list(self._media.values())
        by_path = {m.path: m for m in items}
        ordered: list[MediaInfo] = []
        if self._order:
            ordered = [by_path[p] for p in self._order if p in by_path]
            placed = {m.path for m in ordered}
            ordered += [m for m in items if m.path not in placed]
        items = ordered or items

        previous = {
            row.group.reference.media.path: row.active.media.path for row in self.rows.rows()
        }
        self._groups = group_clips(
            items,
            self._frames,
            self._threshold,
            preserve_input_order=bool(self._order),
            detached=self._detached,
        )
        self._render_rows(previous)
        self._apply_follow_filter()

        if not self.output_edit.text() and items:
            self.output_edit.setText(str(items[0].path.with_name("concat_output.mp4")))
        self._refresh_montage()
        self._update_mode_panel()
        self._update_actions()

    def _refresh_montage(self) -> None:
        """Montaje completo: un tramo por fila visible con su clip activo."""
        self.montage.set_clips(
            [
                (row.active.media, row.active.frames)
                for row in self.rows.rows()
                if not row.isHidden()
            ]
        )

    def _render_rows(self, previous: dict[Path, Path]) -> None:
        self.rows.clear()
        if not self._groups:
            self.empty_hint.show()
            self.rows.hide()
            return

        self.empty_hint.hide()
        self.rows.show()
        for index, group in enumerate(self._groups):
            row = ClipGroupRow(group, index)
            row.selected.connect(self._on_row_selected)
            row.play_requested.connect(self._open_in_system_player)
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

        if self.rows.rows():
            self._on_row_selected(0)

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

    def _keep_visible_selection(self, rows: list[ClipGroupRow]) -> None:
        """La fila seleccionada no puede quedar oculta por el filtro."""
        if not rows:
            return
        selected = next((i for i, row in enumerate(rows) if row.is_selected()), None)
        if selected is not None and not rows[selected].isHidden():
            self._on_row_selected(selected)
            return
        self._on_row_selected(next((i for i, row in enumerate(rows) if not row.isHidden()), 0))

    @Slot(object, bool)
    def _on_follow_toggled(self, media: object, checked: bool) -> None:
        if isinstance(media, MediaInfo):
            anchor = media.path
        else:
            anchor = Path(str(media))
        self._follow_anchor = anchor if checked else None
        log.info("Filtro «mostrar siguientes» → %s", self._follow_anchor or "desactivado")
        self._apply_follow_filter()
        self.status_label.setText(
            f"Mostrando lo que sigue a {anchor.name}" if checked else "Mostrando todos los clips"
        )

    @Slot()
    def _clear_follow_filter(self) -> None:
        if self._follow_anchor is None:
            return
        log.info("Filtro «mostrar siguientes» desactivado desde la cabecera")
        self._follow_anchor = None
        self._apply_follow_filter()
        self.status_label.setText("Mostrando todos los clips")

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
            self.status_label.setText("Ese clip ya está solo en su fila")
            self._sync_detach_switch(path, False)
            return

        order = [p for p in self._current_order() if p != path]
        position = order.index(siblings[-1]) + 1
        order.insert(position, path)
        self._detached.add(path)
        self._order = order

        label = self._media[path].label if path in self._media else path.name
        log.info("Clip separado a fila propia: %s", path)
        self._rebuild_groups()
        self.status_label.setText(f"{label} movido a una fila propia")

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
        self.status_label.setText(f"{label} devuelto a su fila")

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

    @Slot(int)
    def _on_row_selected(self, index: int) -> None:
        if not self.rows.rows():
            return
        for row in self.rows.rows():
            row.set_selected(row.index == index)
        self._refresh_montage()
        self._update_selection_detail()
        self._update_actions()

    @Slot()
    def _on_rows_reordered(self) -> None:
        """Fija el orden manual usando TODOS los miembros, no sólo la referencia.

        Con sólo la referencia, arrastrar una fila con variantes borraba del
        modelo los miembros no activos y el clip desaparecía sin aviso.
        """
        ordered: list[Path] = []
        for row in self.rows.rows():
            ordered.extend(member.media.path for member in row.group.members)
        self._order = ordered
        self._rebuild_groups()

    def _selected_media(self) -> list[MediaInfo]:
        """Un clip por fila visible (el activo), en el orden actual de las filas."""
        return [row.active.media for row in self.rows.rows() if not row.isHidden()]

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
            self._rebuild_groups()

    # --- Acciones sobre clips ---
    def _remove_selection(self) -> None:
        current = next((i for i, row in enumerate(self.rows.rows()) if row.is_selected()), None)
        if current is None or not self._groups:
            return
        group = self._groups[current]
        active = group.resolve(self.rows.rows()[current].active_index)
        remaining = [m for m in group.members if m is not active]
        del self._media[active.media.path]

        if not remaining:
            self._frames.pop(active.media.path, None)
        self._detached.discard(active.media.path)
        self._order = [p for p in self._order if p != active.media.path]
        log.info("Quitado %s (quedan %d en su fila)", active.media.label, len(remaining))
        self._rebuild_groups()
        self.status_label.setText(f"Quitado {active.media.label}")

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
        self.rows.hide()
        self.montage.set_clips([])
        self._update_mode_panel()
        self._update_actions()
        self.status_label.setText("Lista vacía")

    def _open_in_system_player(self, media: object) -> None:
        if not isinstance(media, MediaInfo):
            return
        url = QUrl.fromLocalFile(str(media.path))
        if not QDesktopServices.openUrl(url):
            log.warning("No se pudo abrir %s con el reproductor del sistema", media.path)
            self.status_label.setText(f"No se pudo abrir {media.label}")
            return
        log.info("Reproducción externa: %s", media.path)
        self.status_label.setText(f"Abriendo {media.label} en el reproductor del sistema")

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

        for thread in (self._probe_thread, self._frames_thread, self._concat_thread):
            if thread is not None and thread.isRunning():
                thread.quit()
                if not thread.wait(CLOSE_WAIT_MS):
                    log.warning(
                        "Hilo aún ocupado al cerrar; se espera sin terminar el proceso"
                    )
                    thread.wait(CLOSE_WAIT_MS)
        event.accept()
