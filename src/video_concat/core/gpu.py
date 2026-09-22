"""Detección de aceleración hardware NVIDIA para ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GpuCapabilities:
    """Capacidades reales (no solo presencia de flags en ffmpeg)."""

    nvenc: bool
    cuda_decode: bool
    scale_cuda: bool
    encoder: str | None = None
    reasons: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        """Compat: hay al menos encoder NVENC usable."""
        return self.nvenc

    @property
    def cuda_full(self) -> bool:
        """NVDEC + scale_cuda + NVENC disponibles y verificados."""
        return self.nvenc and self.cuda_decode and self.scale_cuda


def _run(cmd: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _has_filter(ffmpeg: str, name: str) -> bool:
    filters = _run([ffmpeg, "-hide_banner", "-filters"])
    # Líneas tipo: "... scale_cuda        V->V       ..."
    for line in filters.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def _probe_nvenc(ffmpeg: str) -> tuple[bool, str | None]:
    probe = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=0.04",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "-",
        ],
        timeout=15.0,
    )
    if probe.returncode != 0:
        err = (probe.stderr or probe.stdout or "").strip().splitlines()
        return False, err[-1] if err else "error desconocido"
    return True, None


def _probe_cuda_full(ffmpeg: str) -> tuple[bool, str | None]:
    """Decode CUDA + scale_cuda + NVENC sobre un micro-MP4 H.264 real."""
    with tempfile.TemporaryDirectory(prefix="vc-gpu-") as tmp:
        sample = Path(tmp) / "sample.mp4"
        gen = _run(
            [
                ffmpeg,
                "-hide_banner",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=256x256:d=0.12",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                str(sample),
            ],
            timeout=20.0,
        )
        if gen.returncode != 0 or not sample.is_file():
            err = (gen.stderr or "").strip().splitlines()
            return False, err[-1] if err else "no se pudo generar sample H.264"

        probe = _run(
            [
                ffmpeg,
                "-hide_banner",
                "-hwaccel",
                "cuda",
                "-hwaccel_output_format",
                "cuda",
                "-i",
                str(sample),
                "-vf",
                "scale_cuda=w=256:h=256:format=nv12,hwdownload,format=nv12",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            timeout=25.0,
        )
        if probe.returncode != 0:
            err = (probe.stderr or probe.stdout or "").strip().splitlines()
            return False, err[-1] if err else "error desconocido"
    return True, None


@lru_cache(maxsize=1)
def detect_gpu() -> GpuCapabilities:
    reasons: list[str] = []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return GpuCapabilities(False, False, False, None, ("ffmpeg no encontrado en PATH",))

    enc = _run([ffmpeg, "-hide_banner", "-encoders"])
    if "h264_nvenc" not in enc.stdout:
        reasons.append("ffmpeg no incluye el encoder h264_nvenc")
        return GpuCapabilities(False, False, False, None, tuple(reasons))

    nvenc_ok, nvenc_err = _probe_nvenc(ffmpeg)
    if not nvenc_ok:
        reasons.append(f"h264_nvenc no usable: {nvenc_err}")
        return GpuCapabilities(False, False, False, None, tuple(reasons))

    has_scale = _has_filter(ffmpeg, "scale_cuda")
    if not has_scale:
        reasons.append("filtro scale_cuda no disponible")
        return GpuCapabilities(True, False, False, "h264_nvenc", tuple(reasons))

    cuda_ok, cuda_err = _probe_cuda_full(ffmpeg)
    if not cuda_ok:
        reasons.append(f"pipeline CUDA no usable: {cuda_err}")
        return GpuCapabilities(True, False, True, "h264_nvenc", tuple(reasons))

    return GpuCapabilities(True, True, True, "h264_nvenc", ())


def clear_gpu_cache() -> None:
    detect_gpu.cache_clear()
