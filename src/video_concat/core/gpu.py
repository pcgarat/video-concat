"""Detección de aceleración hardware NVIDIA para ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class GpuCapabilities:
    available: bool
    encoder: str | None
    reasons: tuple[str, ...] = ()


def _run(cmd: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@lru_cache(maxsize=1)
def detect_gpu() -> GpuCapabilities:
    reasons: list[str] = []
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return GpuCapabilities(False, None, ("ffmpeg no encontrado en PATH",))

    enc = _run([ffmpeg, "-hide_banner", "-encoders"])
    if "h264_nvenc" not in enc.stdout:
        reasons.append("ffmpeg no incluye el encoder h264_nvenc")
        return GpuCapabilities(False, None, tuple(reasons))

    # Prueba mínima: abre el encoder sin fichero de salida real.
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
        tail = err[-1] if err else "error desconocido"
        reasons.append(f"h264_nvenc no usable: {tail}")
        return GpuCapabilities(False, None, tuple(reasons))

    return GpuCapabilities(True, "h264_nvenc", ())


def clear_gpu_cache() -> None:
    detect_gpu.cache_clear()
