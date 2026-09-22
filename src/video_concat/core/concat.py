"""Ejecución de concatenación con ffmpeg."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from video_concat.core.compatibility import CompatibilityReport, ConcatMode
from video_concat.core.gpu import GpuCapabilities, detect_gpu
from video_concat.core.probe import MediaInfo

ProgressCallback = Callable[[float, str], None]


class ConcatError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ConcatResult:
    output: Path
    mode: ConcatMode
    used_encoder: str
    command: list[str]


def _escape_concat_path(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "\\\\").replace("'", r"\'")
    return f"file '{text}'"


def _write_concat_list(paths: list[Path], list_file: Path) -> None:
    list_file.write_text("\n".join(_escape_concat_path(p) for p in paths) + "\n", encoding="utf-8")


def _build_copy_cmd(list_file: Path, output: Path) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ConcatError("ffmpeg no encontrado en PATH")
    return [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _target_size(ref: MediaInfo) -> tuple[int, int]:
    assert ref.video is not None
    w = ref.video.width or 1920
    h = ref.video.height or 1080
    return w - (w % 2), h - (h % 2)


def _build_reencode_cmd(
    items: list[MediaInfo],
    output: Path,
    report: CompatibilityReport,
    use_nvenc: bool,
) -> tuple[list[str], str]:
    """
    Recodifica con filter_complex (escala/fps/audio normalizados).

    La GPU se usa en el encoder (h264_nvenc). El decode queda en software:
    es el patrón más estable con concat + filter_complex y sigue siendo rápido
    porque el cuello de botella típico es el encode.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ConcatError("ffmpeg no encontrado en PATH")

    ref = report.reference
    if ref is None or ref.video is None:
        raise ConcatError("Sin referencia de vídeo para recodificar")

    width, height = _target_size(ref)
    fps = ref.fps_label if ref.fps_label != "—" else "30"
    n = len(items)

    cmd: list[str] = [ffmpeg, "-hide_banner", "-y"]
    for item in items:
        cmd.extend(["-i", str(item.path)])

    filter_parts: list[str] = []
    concat_labels: list[str] = []

    for i, item in enumerate(items):
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,"
            f"format=yuv420p[v{i}]"
        )
        if item.audio is not None:
            filter_parts.append(
                f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
            )
        else:
            dur = max(item.duration, 0.04)
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a{i}]"
            )
        # concat exige pares [v][a] por segmento, no todos los v y luego los a
        concat_labels.append(f"[v{i}][a{i}]")

    filter_parts.append(f"{''.join(concat_labels)}concat=n={n}:v=1:a=1[outv][outa]")

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
        ]
    )

    if use_nvenc:
        encoder = "h264_nvenc"
        cmd.extend(
            [
                "-c:v",
                encoder,
                "-preset",
                "p4",
                "-rc",
                "vbr",
                "-cq",
                "23",
                "-b:v",
                "0",
                "-profile:v",
                "high",
                "-pix_fmt",
                "yuv420p",
            ]
        )
    else:
        encoder = "libx264"
        cmd.extend(
            [
                "-c:v",
                encoder,
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
            ]
        )

    cmd.extend(["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)])
    return cmd, encoder


def build_command(
    items: list[MediaInfo],
    output: Path,
    report: CompatibilityReport,
    list_file: Path | None = None,
    use_nvenc: bool = False,
) -> tuple[list[str], str]:
    if report.mode is ConcatMode.COPY:
        if list_file is None:
            raise ConcatError("list_file requerido en modo copy")
        _write_concat_list([m.path for m in items], list_file)
        return _build_copy_cmd(list_file, output), "copy"
    return _build_reencode_cmd(items, output, report, use_nvenc=use_nvenc)


class ConcatJob:
    def __init__(
        self,
        items: list[MediaInfo],
        output: Path,
        report: CompatibilityReport,
        on_progress: ProgressCallback | None = None,
        gpu: GpuCapabilities | None = None,
    ) -> None:
        self.items = items
        self.output = output.resolve()
        self.report = report
        self.on_progress = on_progress
        self.gpu = gpu if gpu is not None else detect_gpu()
        self._proc: subprocess.Popen[str] | None = None
        self._cancelled = False
        self.total_duration = sum(m.duration for m in items) or 1.0

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()

    def run(self) -> ConcatResult:
        use_nvenc = self.report.mode is ConcatMode.REENCODE and self.gpu.available
        try:
            return self._run_once(use_nvenc=use_nvenc)
        except ConcatError:
            if use_nvenc and not self._cancelled:
                self._emit(0.0, "NVENC falló; reintentando en CPU…")
                return self._run_once(use_nvenc=False)
            raise

    def _run_once(self, *, use_nvenc: bool) -> ConcatResult:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="video-concat-"))
        list_path = tmp_dir / "concat.txt"
        progress_path = tmp_dir / "progress.txt"

        try:
            cmd, encoder = build_command(
                self.items,
                self.output,
                self.report,
                list_file=list_path if self.report.mode is ConcatMode.COPY else None,
                use_nvenc=use_nvenc,
            )
            out = cmd[-1]
            cmd = [*cmd[:-1], "-progress", str(progress_path), "-nostats", out]

            self._emit(0.0, f"Iniciando ({encoder})…")
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            watcher = threading.Thread(
                target=self._watch_progress,
                args=(progress_path,),
                daemon=True,
            )
            watcher.start()
            _stdout, stderr = self._proc.communicate()
            watcher.join(timeout=1.0)

            if self._cancelled:
                raise ConcatError("Operación cancelada")
            if self._proc.returncode != 0:
                detail = (stderr or "").strip().splitlines()
                tail = "\n".join(detail[-12:]) if detail else "sin detalle"
                raise ConcatError(f"ffmpeg falló (código {self._proc.returncode}):\n{tail}")

            self._emit(1.0, "Completado")
            return ConcatResult(self.output, self.report.mode, encoder, cmd)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _emit(self, value: float, message: str) -> None:
        if self.on_progress:
            self.on_progress(max(0.0, min(1.0, value)), message)

    def _watch_progress(self, progress_path: Path) -> None:
        last_size = 0
        while self._proc and self._proc.poll() is None and not self._cancelled:
            try:
                if progress_path.is_file():
                    data = progress_path.read_text(encoding="utf-8", errors="ignore")
                    if len(data) != last_size:
                        last_size = len(data)
                        out_ms = _parse_out_time_ms(data)
                        if out_ms is not None:
                            ratio = out_ms / (self.total_duration * 1000.0)
                            self._emit(ratio, f"Procesando… {min(ratio, 0.99) * 100:.0f}%")
            except OSError:
                pass
            time.sleep(0.2)


def _parse_out_time_ms(blob: str) -> float | None:
    """ffmpeg escribe microsegundos en out_time_ms pese al nombre."""
    value: float | None = None
    for line in blob.splitlines():
        if line.startswith("out_time_ms="):
            raw = line.split("=", 1)[1].strip()
            try:
                # Valor en microsegundos
                value = float(raw) / 1000.0
            except ValueError:
                continue
        elif line.startswith("out_time_us="):
            raw = line.split("=", 1)[1].strip()
            try:
                value = float(raw) / 1000.0
            except ValueError:
                continue
    return value
