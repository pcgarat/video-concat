"""Decisión copy vs recodificación."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from video_concat.core.probe import MediaInfo, _fps_as_str


class ConcatMode(StrEnum):
    COPY = "copy"
    REENCODE = "reencode"


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    mode: ConcatMode
    reasons: tuple[str, ...]
    reference: MediaInfo | None = None

    @property
    def can_stream_copy(self) -> bool:
        return self.mode is ConcatMode.COPY


def _fps_key(info: MediaInfo) -> str:
    assert info.video is not None
    rate = info.video.avg_frame_rate or info.video.r_frame_rate
    return _fps_as_str(rate)


def analyze_compatibility(items: list[MediaInfo]) -> CompatibilityReport:
    if len(items) < 2:
        return CompatibilityReport(
            ConcatMode.COPY,
            ("Se necesitan al menos dos vídeos",) if len(items) < 2 else (),
            items[0] if items else None,
        )

    ref = items[0]
    assert ref.video is not None
    reasons: list[str] = []

    for other in items[1:]:
        assert other.video is not None
        if other.video.codec_name != ref.video.codec_name:
            reasons.append(
                f"Códec vídeo distinto: {other.label} ({other.video.codec_name}) "
                f"≠ {ref.label} ({ref.video.codec_name})"
            )
        if (other.video.width, other.video.height) != (ref.video.width, ref.video.height):
            reasons.append(
                f"Resolución distinta: {other.label} ({other.resolution}) "
                f"≠ {ref.label} ({ref.resolution})"
            )
        if _fps_key(other) != _fps_key(ref):
            reasons.append(
                f"FPS distinto: {other.label} ({other.fps_label}) "
                f"≠ {ref.label} ({ref.fps_label})"
            )
        if (other.video.pix_fmt or "") != (ref.video.pix_fmt or ""):
            reasons.append(
                f"Pixel format distinto: {other.label} ({other.video.pix_fmt}) "
                f"≠ {ref.label} ({ref.video.pix_fmt})"
            )

        ref_audio = ref.audio
        other_audio = other.audio
        if (ref_audio is None) != (other_audio is None):
            reasons.append(f"Presencia de audio inconsistente en {other.label}")
            continue
        if ref_audio and other_audio:
            if other_audio.codec_name != ref_audio.codec_name:
                reasons.append(
                    f"Códec audio distinto: {other.label} ({other_audio.codec_name}) "
                    f"≠ {ref.label} ({ref_audio.codec_name})"
                )
            if other_audio.sample_rate != ref_audio.sample_rate:
                reasons.append(
                    f"Sample rate distinto: {other.label} ({other_audio.sample_rate}) "
                    f"≠ {ref.label} ({ref_audio.sample_rate})"
                )
            if other_audio.channels != ref_audio.channels:
                reasons.append(
                    f"Canales distintos: {other.label} ({other_audio.channels}) "
                    f"≠ {ref.label} ({ref_audio.channels})"
                )

    # Deduplicar manteniendo orden
    unique = tuple(dict.fromkeys(reasons))
    if unique:
        return CompatibilityReport(ConcatMode.REENCODE, unique, ref)
    return CompatibilityReport(ConcatMode.COPY, (), ref)
