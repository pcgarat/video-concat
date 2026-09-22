"""Análisis de medios con ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StreamInfo:
    codec_name: str
    codec_type: str
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    profile: str | None = None
    level: int | None = None
    avg_frame_rate: str | None = None
    r_frame_rate: str | None = None
    time_base: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: Path
    duration: float
    format_name: str
    video: StreamInfo | None
    audio: StreamInfo | None
    size_bytes: int

    @property
    def label(self) -> str:
        return self.path.name

    @property
    def resolution(self) -> str:
        if not self.video or not self.video.width or not self.video.height:
            return "—"
        return f"{self.video.width}×{self.video.height}"

    @property
    def fps_label(self) -> str:
        if not self.video:
            return "—"
        rate = self.video.avg_frame_rate or self.video.r_frame_rate
        return _fps_as_str(rate) if rate else "—"


def _fps_as_str(rate: str | None) -> str:
    if not rate or rate in {"0/0", "N/A"}:
        return "—"
    if "/" in rate:
        num_s, den_s = rate.split("/", 1)
        try:
            num, den = float(num_s), float(den_s)
            if den == 0:
                return "—"
            value = num / den
        except ValueError:
            return rate
    else:
        try:
            value = float(rate)
        except ValueError:
            return rate
    if abs(value - round(value)) < 0.01:
        return f"{int(round(value))}"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _parse_stream(raw: dict) -> StreamInfo:
    level = raw.get("level")
    sample_rate = raw.get("sample_rate")
    channels = raw.get("channels")
    return StreamInfo(
        codec_name=raw.get("codec_name") or "unknown",
        codec_type=raw.get("codec_type") or "unknown",
        width=raw.get("width"),
        height=raw.get("height"),
        pix_fmt=raw.get("pix_fmt"),
        profile=raw.get("profile"),
        level=int(level) if level is not None else None,
        avg_frame_rate=raw.get("avg_frame_rate"),
        r_frame_rate=raw.get("r_frame_rate"),
        time_base=raw.get("time_base"),
        sample_rate=int(sample_rate) if sample_rate else None,
        channels=int(channels) if channels else None,
        channel_layout=raw.get("channel_layout"),
    )


def probe_file(path: Path) -> MediaInfo:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise ProbeError("ffprobe no encontrado en PATH")

    path = path.resolve()
    if not path.is_file():
        raise ProbeError(f"No existe el fichero: {path}")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe agotó el tiempo en {path.name}") from exc
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or f"ffprobe falló en {path.name}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Salida ffprobe inválida: {path.name}") from exc

    streams = [_parse_stream(s) for s in data.get("streams", [])]
    video = next((s for s in streams if s.codec_type == "video"), None)
    audio = next((s for s in streams if s.codec_type == "audio"), None)
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or 0.0)
    size_bytes = int(fmt.get("size") or path.stat().st_size)

    if video is None:
        raise ProbeError(f"Sin pista de vídeo: {path.name}")

    return MediaInfo(
        path=path,
        duration=duration,
        format_name=fmt.get("format_name") or "",
        video=video,
        audio=audio,
        size_bytes=size_bytes,
    )


def probe_many(paths: list[Path], max_workers: int = 4) -> list[MediaInfo]:
    if not paths:
        return []
    workers = min(max_workers, len(paths))
    results: dict[Path, MediaInfo | Exception] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe_file, p): p for p in paths}
        for fut in as_completed(futures):
            path = futures[fut]
            try:
                results[path] = fut.result()
            except Exception as exc:  # noqa: BLE001 — se repropaga ordenado
                results[path] = exc

    ordered: list[MediaInfo] = []
    for path in paths:
        item = results[path]
        if isinstance(item, Exception):
            raise item
        ordered.append(item)
    return ordered
