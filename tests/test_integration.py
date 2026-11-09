"""Integration test: runs actual FFmpeg if installed."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from yingxu.engine import probe_media, run_job
from yingxu.models import Preset, make_jobs


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


if __name__ == "__main__":
    unittest.main()
