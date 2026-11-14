import math
from pathlib import Path

import pytest

from video_concat.core.frames import (
    FRAME_SIZE,
    GRID,
    FrameExtractError,
    FrameSignature,
    extract_edge_frames,
    signature_from_rgb,
    similarity,
)
from video_concat.core.grouping import GroupedClip, group_clips
from video_concat.core.probe import MediaInfo, StreamInfo


def _media(name: str) -> MediaInfo:
    return MediaInfo(
        path=Path(name),
        duration=5.0,
        format_name="mov,mp4",
        video=StreamInfo(codec_name="h264", codec_type="video", width=480, height=640),
        audio=None,
        size_bytes=1,
    )


def _flat(value: float, rgb: tuple[float, float, float] | None = None) -> FrameSignature:
    base = rgb if rgb is not None else (value, value, value)
    return FrameSignature(rgb=tuple([*base] * (GRID * GRID)))


def _frames(first: FrameSignature, last: FrameSignature):
    from video_concat.core.frames import EdgeFrames

    return EdgeFrames(first=first, last=last, preview_rgb=b"\x00" * (FRAME_SIZE * FRAME_SIZE * 3))


def _solid_rgb(value: int) -> bytes:
    return bytes([value, value, value]) * (FRAME_SIZE * FRAME_SIZE)


class TestSimilarity:
    def test_identical_frames_score_one(self):
        frame = _flat(0.4)
        assert similarity(frame, frame) == pytest.approx(1.0)

    def test_opposite_luminance_scores_zero(self):
        assert similarity(_flat(0.0), _flat(1.0)) == pytest.approx(0.0)

    def test_color_shift_lowers_score_even_with_same_structure(self):
        base = _flat(0.0, (0.40, 0.50, 0.55))
        warm = _flat(0.0, (0.60, 0.50, 0.45))
        assert similarity(base, warm) < 1.0
        assert similarity(base, warm) > 0.5

    def test_mismatched_grids_rejected(self):
        with pytest.raises(ValueError):
            similarity(FrameSignature((0.0,)), _flat(0.5))

    def test_score_stays_within_bounds(self):
        assert 0.0 <= similarity(_flat(0.1), _flat(0.9)) <= 1.0

    def test_small_shift_stays_above_threshold(self):
        assert similarity(_flat(0.50), _flat(0.53)) >= 0.93


class TestSignatureFromRgb:
    def test_solid_frame_is_uniform(self):
        sig = signature_from_rgb(_solid_rgb(200))
        assert len(sig.rgb) == GRID * GRID * 3
        assert len(set(sig.rgb)) == 1
        assert sig.rgb[0] == pytest.approx(200 / 255)
        assert sig.mean_rgb == pytest.approx((200 / 255, 200 / 255, 200 / 255))

    def test_rejects_wrong_buffer_size(self):
        with pytest.raises(FrameExtractError):
            signature_from_rgb(b"\x00" * 10)

    def test_black_vs_white_are_far_apart(self):
        black = signature_from_rgb(_solid_rgb(0))
        white = signature_from_rgb(_solid_rgb(255))
        assert similarity(black, white) < 0.1


class TestExtractEdgeFrames:
    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FrameExtractError):
            extract_edge_frames(tmp_path / "nope.mp4")

    @pytest.mark.skipif(
        not Path("/usr/bin/ffmpeg").exists() and not Path("/usr/local/bin/ffmpeg").exists(),
        reason="ffmpeg no disponible",
    )
    def test_extracts_both_edges_from_generated_clip(self, tmp_path: Path):
        import shutil
        import subprocess

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("ffmpeg no disponible")
        clip = tmp_path / "clip.mp4"
        subprocess.run(
            [
                ffmpeg, "-v", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=64x64:d=0.5",
                "-pix_fmt", "yuv420p", str(clip),
            ],
            check=True,
        )
        edges = extract_edge_frames(clip, duration=0.5)
        assert len(edges.first.rgb) == GRID * GRID * 3
        assert similarity(edges.first, edges.last) > 0.9
        assert len(edges.preview_rgb) == FRAME_SIZE * FRAME_SIZE * 3


class TestGrouping:
    def test_empty_input(self):
        assert group_clips([]) == []

    def test_clips_sharing_first_and_last_are_one_row(self):
        a, b = _media("a.mp4"), _media("b.mp4")
        same = _frames(_flat(0.30), _flat(0.70))
        groups = group_clips([a, b], {a.path: same, b.path: same})
        assert len(groups) == 1
        assert {g.media.path.name for g in groups[0].members} == {"a.mp4", "b.mp4"}
        assert groups[0].score >= 0.995

    def test_same_last_but_different_first_stays_separate(self):
        """Dos clips que acaban igual pero empiezan distinto no son variantes."""
        a, b = _media("a.mp4"), _media("b.mp4")
        frames = {
            a.path: _frames(_flat(0.10), _flat(0.90)),
            b.path: _frames(_flat(0.80), _flat(0.90)),
        }
        groups = group_clips([a, b], frames)
        assert len(groups) == 2

    def test_near_duplicate_merges_with_threshold(self):
        a, b = _media("a.mp4"), _media("b.mp4")
        frames = {
            a.path: _frames(_flat(0.50), _flat(0.60)),
            b.path: _frames(_flat(0.53), _flat(0.63)),
        }
        assert len(group_clips([a, b], frames, threshold=0.93)) == 1
        assert len(group_clips([a, b], frames, threshold=0.999)) == 2

    def test_threshold_is_respected_on_both_edges(self):
        a, b = _media("a.mp4"), _media("b.mp4")
        frames = {
            a.path: _frames(_flat(0.50), _flat(0.90)),
            b.path: _frames(_flat(0.52), _flat(0.20)),
        }
        assert len(group_clips([a, b], frames, threshold=0.93)) == 2

    def test_chain_is_ordered_by_continuity(self):
        """last -> first encadena las filas en orden narrativo."""
        clips = [_media(f"{i}.mp4") for i in range(3)]
        # 0 termina donde empieza 1, 1 termina donde empieza 2.
        frames = {
            clips[0].path: _frames(_flat(0.10), _flat(0.50)),
            clips[1].path: _frames(_flat(0.50), _flat(0.80)),
            clips[2].path: _frames(_flat(0.80), _flat(0.95)),
        }
        groups = group_clips(clean := [clips[2], clips[0], clips[1]], frames)
        assert len(groups) == 3
        assert clean is not None
        names = [g.reference.media.path.name for g in groups]
        assert names == ["0.mp4", "1.mp4", "2.mp4"]

    def test_row_goes_before_the_one_it_ends_like(self):
        """Si el último de A coincide con el último de B, A precede a B."""
        a, b, c = _media("a.mp4"), _media("b.mp4"), _media("c.mp4")
        frames = {
            a.path: _frames(_flat(0.10), _flat(0.70)),
            b.path: _frames(_flat(0.20), _flat(0.70)),
            c.path: _frames(_flat(0.90), _flat(0.95)),
        }
        groups = group_clips([b, a, c], frames)
        names = [g.reference.media.path.name for g in groups]
        assert names.index("a.mp4") < names.index("b.mp4")

    def test_unanalysed_clips_survive_as_own_rows(self):
        a, b = _media("a.mp4"), _media("b.mp4")
        groups = group_clips([a, b], {a.path: _frames(_flat(0.3), _flat(0.6))})
        assert len(groups) == 2
        pending = [g for g in groups if g.reference.problem]
        assert len(pending) == 1
        assert pending[0].origin == "Fotogramas aún sin analizar"

    def test_extraction_errors_become_problem_rows(self):
        a = _media("a.mp4")
        groups = group_clips([a], {a.path: FrameExtractError("boom")})
        assert len(groups) == 1
        assert "boom" in groups[0].reference.problem

    def test_no_clip_is_lost(self):
        clips = [_media(f"{i}.mp4") for i in range(6)]
        frames = {
            clip.path: _frames(_flat(i / 10), _flat(i / 10 + 0.4))
            for i, clip in enumerate(clips)
        }
        groups = group_clips(clips, frames)
        assert sum(len(g.members) for g in groups) == len(clips)
        assert len({m.media.path for g in groups for m in g.members}) == len(clips)

    def test_resolve_falls_back_out_of_range(self):
        a = _media("a.mp4")
        group = group_clips([a], {a.path: _frames(_flat(0.1), _flat(0.2))})[0]
        assert group.resolve(0) is group.members[0]
        assert group.resolve(5) is group.members[0]
        assert group.resolve(-1) is group.members[0]

    def test_similarity_is_finite_for_bounds(self):
        a = _media("a.mp4")
        frames = {a.path: _frames(_flat(0.0, (0.0, 0.0, 0.0)), _flat(1.0, (1.0, 1.0, 1.0)))}
        group = group_clips([a], frames)[0]
        assert math.isfinite(group.score)


def test_grouped_clip_comparable_flag():
    assert GroupedClip(_media("a.mp4")).comparable is False
