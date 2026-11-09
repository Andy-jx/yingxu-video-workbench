"""Integration test: runs actual FFmpeg if installed."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from yingxu.engine import probe_media, run_job
from yingxu.models import MaskSpec, Preset, ProcessOptions, RangeSpec, WatermarkSpec, make_jobs
from yingxu.processing import extract_frame, render_still


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
class FFmpegTests(unittest.TestCase):
    def test_real_encode_and_validation_and_collision(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            src = root / "input.mp4"
            ffmpeg = shutil.which("ffmpeg")
            ffprobe = shutil.which("ffprobe")
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1.5",
                            "-f", "lavfi", "-i", "sine=frequency=800:duration=1.5",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            "-shortest", str(src)], check=True)
            original_bytes = src.read_bytes()
            out = root / "output"
            preset = Preset("test", (-0.005, 0.005), (0.99, 1.01),
                            (0.99, 1.01), (-0.1, 0.1))
            jobs = make_jobs([src], out, 2, 42, preset)
            first = run_job(jobs[0])
            second = run_job(jobs[1])
            collision = run_job(jobs[0])
            self.assertEqual(src.read_bytes(), original_bytes)
            self.assertEqual(len({first.output, second.output, collision.output}), 3)
            self.assertNotEqual(first.parameters, second.parameters)
            for result in (first, second, collision):
                data = probe_media(result.output, ffprobe)
                self.assertGreater(data["duration"], 1.0)
                self.assertGreater(data["audio_count"], 0)

    def test_watermark_mask_speed_and_chinese_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "素材目录"
            folder.mkdir()
            src = folder / "样片.mp4"
            ffmpeg = shutil.which("ffmpeg")
            ffprobe = shutil.which("ffprobe")
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=1.6",
                            "-f", "lavfi", "-i", "sine=frequency=660:duration=1.6",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            "-shortest", str(src)], check=True)
            original = src.read_bytes()
            out = root / "导出"
            preset = Preset("test")
            plain = make_jobs([src], out, 1, 1, preset, False, ProcessOptions())[0]
            marked = ProcessOptions(
                volume_db=RangeSpec(True, -1.0, -1.0),
                watermark=WatermarkSpec(enabled=True, style="text", text="影序", size=22, opacity=0.9,
                                        x=0.05, y=0.08),
                masks=(MaskSpec(mode="blur", x=0.0, y=0.72, w=1.0, h=0.22, blur=10,
                                feather=4, start=0.7, end=1.3),),
            )
            rich = make_jobs([src], out, 1, 2, preset, False, marked)[0]
            fast = make_jobs([src], out, 1, 3, preset, False,
                             ProcessOptions(speed=RangeSpec(True, 1.25, 1.25)))[0]
            plain_result = run_job(plain)
            rich_result = run_job(rich)
            fast_result = run_job(fast)
            self.assertEqual(src.read_bytes(), original)
            self.assertEqual(len({plain_result.output, rich_result.output, fast_result.output}), 3)
            for result in (plain_result, rich_result, fast_result):
                data = probe_media(result.output, ffprobe)
                self.assertGreater(data["audio_count"], 0)
                self.assertEqual(data["width"], 160)
            self.assertAlmostEqual(probe_media(fast_result.output, ffprobe)["duration"], 1.28, delta=0.25)
            self.assertTrue(fast_result.parameters["forced_full_verify"])
            self.assertIn("watermark", rich_result.parameters)
            preview = root / "preview.png"
            media = probe_media(src, ffprobe)
            render_still(ffmpeg, src, media, marked, 0.9, preview, rich.derived_seed())
            self.assertGreater(preview.stat().st_size, 500)
            before = root / "before.png"
            extract_frame(ffmpeg, rich_result.output, 0.2, before)
            during = root / "during.png"
            extract_frame(ffmpeg, rich_result.output, 1.0, during)
            self.assertNotEqual(before.read_bytes(), during.read_bytes())


if __name__ == "__main__":
    unittest.main()
