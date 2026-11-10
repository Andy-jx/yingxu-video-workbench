"""Live preview helpers and the built-in watermark logo."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from yingxu.models import ProcessOptions, WatermarkSpec
from yingxu.processing import (
    DEFAULT_TEXT_OPACITY, DEFAULT_TEXT_SIZE, apply_default_logo, default_logo_path, extract_frame,
    find_cjk_font, output_time_to_source, prepare_preview_options, render_preview_movie, render_still,
    source_time_to_output, visual_effects,
)


class PreviewTests(unittest.TestCase):
    def test_default_logo_is_a_png_and_used_when_the_path_is_empty(self):
        path = default_logo_path()
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        options = apply_default_logo(ProcessOptions(watermark=WatermarkSpec(enabled=True, style="image", image="")))
        self.assertEqual(options.watermark.image, str(path))
        kept = apply_default_logo(ProcessOptions(watermark=WatermarkSpec(enabled=True, style="image", image=str(path))))
        self.assertEqual(kept.watermark.image, str(path))
        untouched = apply_default_logo(ProcessOptions(watermark=WatermarkSpec(enabled=True, style="text", text="影序")))
        self.assertEqual(untouched.watermark.image, "")

    def test_incomplete_watermark_does_not_block_other_preview_effects(self):
        options = ProcessOptions(
            watermark=WatermarkSpec(enabled=True, style="tile", text=""),
            mirror_h=True,
        )
        prepared = prepare_preview_options(options)
        self.assertFalse(prepared.watermark.enabled)
        self.assertTrue(prepared.mirror_h)
        self.assertTrue(visual_effects(prepared))
        self.assertFalse(visual_effects(ProcessOptions()))

    def test_mask_end_zero_lasts_until_the_video_ends(self):
        from yingxu.models import MaskSpec
        from yingxu.processing import build_plan, sample_options
        media = {"width": 320, "height": 180, "duration": 10.0, "fps": 25.0,
                 "video": {"index": 0}, "audio_count": 0}
        open_ended = ProcessOptions(masks=(MaskSpec(mode="blur", x=0, y=0.7, w=1, h=0.2,
                                                    blur=8, feather=0, start=1, end=0),))
        graph = build_plan(open_ended, sample_options(open_ended, 1), media, seed=1).graph
        self.assertIn("gblur", graph)
        self.assertIn("gte(t,", graph)
        self.assertNotIn("between(t,1.000,0.000)", graph)
        late = build_plan(open_ended, sample_options(open_ended, 1), media, preview_time=8, include_audio=False, seed=1)
        self.assertIn("gblur", late.graph)
        early = build_plan(open_ended, sample_options(open_ended, 1), media, preview_time=0.2, include_audio=False, seed=1)
        self.assertNotIn("gblur", early.graph)

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires FFmpeg")
    def test_default_logo_is_visible_at_the_builtin_size(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "gray.mp4"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "color=c=0x808080:s=320x180:d=0.2",
                            "-frames:v", "1", str(source)], check=True)
            from yingxu.engine import probe_media
            from yingxu.processing import DEFAULT_LOGO_OPACITY, DEFAULT_LOGO_SIZE
            media = probe_media(source, shutil.which("ffprobe"))
            plain = root / "plain.png"
            marked = root / "logo.png"
            extract_frame(ffmpeg, source, 0, plain)
            render_still(ffmpeg, source, media, ProcessOptions(watermark=WatermarkSpec(
                enabled=True, style="image", image=str(default_logo_path()),
                size=DEFAULT_LOGO_SIZE, opacity=DEFAULT_LOGO_OPACITY, x=0.04, y=0.04,
            )), 0, marked, 1, max_edge=0)
            raw_plain = root / "a.raw"
            raw_marked = root / "b.raw"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(plain),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw_plain)], check=True)
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(marked),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw_marked)], check=True)
            difference = max(abs(a - b) for a, b in zip(raw_plain.read_bytes(), raw_marked.read_bytes()))
            self.assertGreater(difference, 40)

    def test_default_text_watermark_is_large_and_stroked(self):
        from yingxu.processing import build_plan, sample_options
        self.assertGreaterEqual(DEFAULT_TEXT_SIZE, 64)
        self.assertGreaterEqual(DEFAULT_TEXT_OPACITY, 0.85)
        media = {"width": 320, "height": 180, "duration": 2.0, "fps": 25.0,
                 "video": {"index": 0}, "audio_count": 0}
        options = ProcessOptions(watermark=WatermarkSpec(
            enabled=True, style="text", text="弄好", size=DEFAULT_TEXT_SIZE, opacity=DEFAULT_TEXT_OPACITY,
        ))
        graph = build_plan(options, sample_options(options, 1), media, seed=1).graph
        self.assertIn("弄好", graph)
        self.assertIn(f"fontsize={DEFAULT_TEXT_SIZE}", graph)
        self.assertNotIn("borderw=1", graph)
        self.assertIn("borderw=", graph)
        self.assertAlmostEqual(source_time_to_output(2.0, 2.0, [], 0.0), 1.0)
        self.assertAlmostEqual(output_time_to_source(1.0, 2.0, [], 0.0, 5.0), 2.0)

    @unittest.skipUnless(shutil.which("ffmpeg") and find_cjk_font(), "requires FFmpeg and a CJK font")
    def test_default_text_watermark_is_visible_on_a_frame(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "gray.mp4"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "color=c=0x808080:s=320x180:d=0.2",
                            "-frames:v", "1", str(source)], check=True)
            from yingxu.engine import probe_media
            media = probe_media(source, shutil.which("ffprobe"))
            plain = root / "plain.png"
            marked = root / "text.png"
            extract_frame(ffmpeg, source, 0, plain)
            render_still(ffmpeg, source, media, ProcessOptions(
                font=find_cjk_font() or "",
                watermark=WatermarkSpec(
                    enabled=True, style="text", text="弄好", size=DEFAULT_TEXT_SIZE,
                    opacity=DEFAULT_TEXT_OPACITY, x=0.04, y=0.06,
                ),
            ), 0, marked, 1, max_edge=0)
            raw_plain = root / "a.raw"
            raw_marked = root / "b.raw"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(plain),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw_plain)], check=True)
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(marked),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw_marked)], check=True)
            difference = max(abs(a - b) for a, b in zip(raw_plain.read_bytes(), raw_marked.read_bytes()))
            self.assertGreater(difference, 40)

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires FFmpeg")
    def test_preview_playback_exports_a_movie(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "gray.mp4"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "color=c=0x406080:s=320x180:d=0.4",
                            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                            "-shortest", str(source)], check=True)
            from yingxu.engine import probe_media
            media = probe_media(source, shutil.which("ffprobe"))
            output = root / "preview.mp4"
            info = render_preview_movie(ffmpeg, source, media, ProcessOptions(watermark=WatermarkSpec(
                enabled=True, style="text", text="弄好", size=DEFAULT_TEXT_SIZE, opacity=DEFAULT_TEXT_OPACITY,
                font=find_cjk_font() or "",
            )), output, 1)
            self.assertGreater(output.stat().st_size, 1000)
            self.assertGreater(info["length"], 0.2)
            self.assertEqual(output.suffix, ".mp4")

    @unittest.skipUnless(shutil.which("ffmpeg"), "requires FFmpeg")
    def test_still_preview_shows_watermark_and_photo_insert(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "in.mp4"
            photo = root / "photo.png"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1.2",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                           check=True)
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "color=c=blue:s=40x20", "-frames:v", "1", str(photo)], check=True)
            plain = root / "plain.png"
            marked = root / "marked.png"
            extract_frame(ffmpeg, source, 0.2, plain)
            from yingxu.engine import probe_media
            media = probe_media(source, shutil.which("ffprobe"))
            render_still(ffmpeg, source, media, ProcessOptions(
                watermark=WatermarkSpec(enabled=True, style="text", text="影序", opacity=1, size=28, x=0.05, y=0.05),
            ), 0.2, marked, seed=1, max_edge=0)
            self.assertNotEqual(plain.read_bytes(), marked.read_bytes())
            from yingxu.models import PhotoInsertSpec
            inserted = root / "inserted.png"
            render_still(ffmpeg, source, media, ProcessOptions(photos=PhotoInsertSpec(
                enabled=True, paths=(str(photo),), interval=0.4, interval_jitter=0,
                hold_ms=300, hold_jitter_ms=0, fade_ms=0,
            )), 0.45, inserted, seed=2, max_edge=0)
            self.assertTrue(inserted.is_file())
            self.assertGreater(inserted.stat().st_size, 100)
            self.assertNotEqual(plain.read_bytes(), inserted.read_bytes())


if __name__ == "__main__":
    unittest.main()
