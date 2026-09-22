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
from enum import StrEnum
from pathlib import Path

from video_concat.core.compatibility import CompatibilityReport, ConcatMode
from video_concat.core.gpu import GpuCapabilities, detect_gpu
from video_concat.core.probe import MediaInfo

ProgressCallback = Callable[[float, str], None]


class ConcatError(RuntimeError):
    pass


class Pipeline(StrEnum):
    """Estrategia de proceso de vídeo."""

    COPY = "copy"
    CUDA_FULL = "cuda_full"  # NVDEC + scale_cuda + NVENC
    NVENC = "nvenc"  # decode/scale CPU + NVENC
    CPU = "cpu"  # libx264


@dataclass(frozen=True, slots=True)
class ConcatResult:
    output: Path
    mode: ConcatMode
    used_encoder: str
    pipeline: Pipeline
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


def _audio_filter(i: int, item: MediaInfo) -> str:
    if item.audio is not None:
        return (
            f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
    dur = max(item.duration, 0.04)
    return f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a{i}]"


def _video_filter_cpu(i: int, width: int, height: int, fps: str) -> str:
    return (
        f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,"
        f"format=yuv420p[v{i}]"
    )


def _video_filter_cuda(i: int, width: int, height: int, fps: str) -> str:
    """
    NVDEC deja frames CUDA → scale_cuda en GPU → hwdownload mínimo para
    pad/fps/concat (esos filtros no operan sobre frames CUDA en ffmpeg).
    """
    return (
        f"[{i}:v]scale_cuda=w={width}:h={height}:"
        f"force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"hwdownload,format=nv12,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},setsar=1,format=yuv420p[v{i}]"
    )


def _nvenc_args() -> list[str]:
    return [
        "-c:v",
        "h264_nvenc",
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


def _x264_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
    ]


def _build_reencode_cmd(
    items: list[MediaInfo],
    output: Path,
    report: CompatibilityReport,
    pipeline: Pipeline,
) -> tuple[list[str], str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ConcatError("ffmpeg no encontrado en PATH")

    ref = report.reference
    if ref is None or ref.video is None:
        raise ConcatError("Sin referencia de vídeo para recodificar")
    if pipeline is Pipeline.COPY:
        raise ConcatError("pipeline COPY no aplica a recodificación")

    width, height = _target_size(ref)
    fps = ref.fps_label if ref.fps_label != "—" else "30"
    n = len(items)
    use_cuda = pipeline is Pipeline.CUDA_FULL

    cmd: list[str] = [ffmpeg, "-hide_banner", "-y"]
    for item in items:
        if use_cuda:
            cmd.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        cmd.extend(["-i", str(item.path)])

    filter_parts: list[str] = []
    concat_labels: list[str] = []
    for i, item in enumerate(items):
        if use_cuda:
            filter_parts.append(_video_filter_cuda(i, width, height, fps))
        else:
            filter_parts.append(_video_filter_cpu(i, width, height, fps))
        filter_parts.append(_audio_filter(i, item))
        concat_labels.append(f"[v{i}][a{i}]")

    filter_parts.append(f"{''.join(concat_labels)}concat=n={n}:v=1:a=1[outv][outa]")
    cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[outv]", "-map", "[outa]"])

    if pipeline in {Pipeline.CUDA_FULL, Pipeline.NVENC}:
        encoder = "h264_nvenc+cuda" if use_cuda else "h264_nvenc"
        cmd.extend(_nvenc_args())
    else:
        encoder = "libx264"
        cmd.extend(_x264_args())

    cmd.extend(["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output)])
    return cmd, encoder


def select_pipeline(report: CompatibilityReport, gpu: GpuCapabilities) -> Pipeline:
    if report.mode is ConcatMode.COPY:
        return Pipeline.COPY
    if gpu.cuda_full:
        return Pipeline.CUDA_FULL
    if gpu.nvenc:
        return Pipeline.NVENC
    return Pipeline.CPU


def fallback_pipeline(current: Pipeline) -> Pipeline | None:
    if current is Pipeline.CUDA_FULL:
        return Pipeline.NVENC
    if current is Pipeline.NVENC:
        return Pipeline.CPU
    return None


def build_command(
    items: list[MediaInfo],
    output: Path,
    report: CompatibilityReport,
    pipeline: Pipeline,
    list_file: Path | None = None,
) -> tuple[list[str], str]:
    if pipeline is Pipeline.COPY:
        if list_file is None:
            raise ConcatError("list_file requerido en modo copy")
        _write_concat_list([m.path for m in items], list_file)
        return _build_copy_cmd(list_file, output), "copy"
    return _build_reencode_cmd(items, output, report, pipeline)


class ConcatJob:
    def __init__(
        self,
        items: list[MediaInfo],
        output: Path,
        report: CompatibilityReport,
        on_progress: ProgressCallback | None = None,
        gpu: GpuCapabilities | None = None,
        pipeline: Pipeline | None = None,
    ) -> None:
        self.items = items
        self.output = output.resolve()
        self.report = report
        self.on_progress = on_progress
        self.gpu = gpu if gpu is not None else detect_gpu()
        self.pipeline = pipeline if pipeline is not None else select_pipeline(report, self.gpu)
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
        pipeline = self.pipeline
        last_error: ConcatError | None = None
        while pipeline is not None:
            try:
                return self._run_once(pipeline)
            except ConcatError as exc:
                last_error = exc
                if self._cancelled:
                    raise
                nxt = fallback_pipeline(pipeline)
                if nxt is None:
                    raise
                self._emit(0.0, f"{pipeline} falló; reintentando con {nxt}…")
                pipeline = nxt
        assert last_error is not None
        raise last_error

    def _run_once(self, pipeline: Pipeline) -> ConcatResult:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="video-concat-"))
        list_path = tmp_dir / "concat.txt"
        progress_path = tmp_dir / "progress.txt"

        try:
            cmd, encoder = build_command(
                self.items,
                self.output,
                self.report,
                pipeline,
                list_file=list_path if pipeline is Pipeline.COPY else None,
            )
            out = cmd[-1]
            cmd = [*cmd[:-1], "-progress", str(progress_path), "-nostats", out]

            self._emit(0.0, f"Iniciando ({pipeline}: {encoder})…")
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
            return ConcatResult(self.output, self.report.mode, encoder, pipeline, cmd)
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
