"""Live preview helpers and the built-in watermark logo."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from yingxu.models import ProcessOptions, WatermarkSpec
from yingxu.processing import (
    apply_default_logo, default_logo_path, extract_frame, prepare_preview_options, render_still,
    visual_effects,
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
