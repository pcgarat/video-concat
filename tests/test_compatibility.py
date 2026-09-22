from pathlib import Path

from video_concat.core.compatibility import ConcatMode, analyze_compatibility
from video_concat.core.concat import _escape_concat_path, _parse_out_time_ms
from video_concat.core.probe import MediaInfo, StreamInfo


def _media(
    name: str,
    *,
    codec: str = "h264",
    w: int = 1920,
    h: int = 1080,
    fps: str = "30/1",
    pix: str = "yuv420p",
    acodec: str | None = "aac",
    rate: int | None = 48000,
    channels: int | None = 2,
    duration: float = 10.0,
) -> MediaInfo:
    video = StreamInfo(
        codec_name=codec,
        codec_type="video",
        width=w,
        height=h,
        pix_fmt=pix,
        avg_frame_rate=fps,
    )
    audio = None
    if acodec:
        audio = StreamInfo(
            codec_name=acodec,
            codec_type="audio",
            sample_rate=rate,
            channels=channels,
        )
    return MediaInfo(
        path=Path(name),
        duration=duration,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        video=video,
        audio=audio,
        size_bytes=1_000_000,
    )


def test_compatible_uses_copy():
    items = [_media("a.mp4"), _media("b.mp4")]
    report = analyze_compatibility(items)
    assert report.mode is ConcatMode.COPY
    assert report.reasons == ()


def test_resolution_mismatch_forces_reencode():
    items = [_media("a.mp4", w=1920), _media("b.mp4", w=1280, h=720)]
    report = analyze_compatibility(items)
    assert report.mode is ConcatMode.REENCODE
    assert any("Resolución" in r for r in report.reasons)


def test_audio_mismatch_forces_reencode():
    items = [_media("a.mp4", rate=48000), _media("b.mp4", rate=44100)]
    report = analyze_compatibility(items)
    assert report.mode is ConcatMode.REENCODE


def test_escape_concat_path_quotes():
    line = _escape_concat_path(Path("/tmp/my video's.mp4"))
    assert line.startswith("file '")
    assert r"\'" in line or "video" in line


def test_parse_out_time_ms():
    blob = "out_time_ms=2500000\nprogress=continue\n"
    # 2_500_000 µs → 2500 ms
    assert _parse_out_time_ms(blob) == 2500.0
