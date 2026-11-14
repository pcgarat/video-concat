"""Fixtures de vídeo sintéticas para los tests (no dependen de samples gitignorados)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FFMPEG = shutil.which("ffmpeg")


def make_clip(
    path: Path,
    *,
    duration: float = 1.0,
    size: str = "160x120",
    first_color: str = "red",
    last_color: str = "blue",
    rate: int = 16,
) -> Path:
    """
    Genera un MP4 cuyo primer tramo es de un color y el último de otro.

    Los tests de agrupado necesitan extremos distinguibles: con un clip de
    color uniforme el primer y el último fotograma son iguales y una regresión
    en la extracción del último pasaría desapercibida.
    """
    if not FFMPEG:
        pytest.skip("ffmpeg no disponible")
    half = max(duration / 2, 0.05)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={first_color}:s={size}:d={half}:r={rate}",
            "-f", "lavfi", "-i", f"color=c={last_color}:s={size}:d={half}:r={rate}",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


def make_solid_clip(path: Path, *, color: str = "gray", duration: float = 0.5) -> Path:
    """Clip de un solo color, para casos donde los extremos deben coincidir."""
    if not FFMPEG:
        pytest.skip("ffmpeg no disponible")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=160x120:d={duration}:r=16",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def clips_factory(tmp_path: Path):
    """Devuelve una factoría que crea clips sintéticos en `tmp_path`."""
    created: list[Path] = []

    def build(name: str, **kwargs) -> Path:
        target = tmp_path / name
        make_clip(target, **kwargs)
        created.append(target)
        return target

    yield build


@pytest.fixture
def solid_factory(tmp_path: Path):
    created: list[Path] = []

    def build(name: str, **kwargs) -> Path:
        target = tmp_path / name
        make_solid_clip(target, **kwargs)
        created.append(target)
        return target

    yield build
