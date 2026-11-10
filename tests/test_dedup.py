"""Picture-in-picture and photo-insert planning. No display required."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yingxu.models import PhotoInsertSpec, PipSpec, ProcessOptions, make_jobs, Preset
from yingxu.processing import (
    build_plan, command_from_plan, dedup_sample, locate_output_time, photo_insert_times,
    sample_options,
)


MEDIA = {"width": 320, "height": 180, "duration": 10.0, "fps": 25.0,
         "video": {"index": 0}, "audio_count": 1}


class DedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.video = self.root / "overlay.mp4"
        self.video.write_bytes(b"video")
        self.photo_a = self.root / "a.jpg"
        self.photo_b = self.root / "b.png"
        self.photo_a.write_bytes(b"a")
        self.photo_b.write_bytes(b"b")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_disabled_dedup_is_absent_from_the_graph(self):
        graph = self._graph(ProcessOptions())
        self.assertNotIn("colorchannelmixer", graph)
        self.assertNotIn("concat=", graph)
        self.assertEqual(graph, "")

    def test_pip_is_opt_in_and_muted_by_default(self):
        options = ProcessOptions(pip=PipSpec(enabled=True, path=str(self.video), opacity=0.1,
                                             opacity_jitter=0, position_jitter=0, audio_percent=0))
        plan = self._plan(options, seed=4)
        self.assertIn("overlay=", plan.graph)
        self.assertIn("colorchannelmixer=aa=", plan.graph)
        self.assertNotIn("amix=", plan.graph)
        self.assertNotIn(":a]", plan.graph)
        command = command_from_plan("ffmpeg", "in.mp4", "out.mp4", plan, MEDIA, options)
        self.assertNotIn(f"{self.video}:a", " ".join(command))
        self.assertTrue(any(item[0] == "pip_loop" for item in plan.extra_inputs))

    def test_pip_audio_percent_is_mixed(self):
        options = ProcessOptions(pip=PipSpec(enabled=True, path=str(self.video), audio_percent=1,
                                             opacity_jitter=0, position_jitter=0, short_mode="hold"))
        graph = self._graph(options)
        self.assertIn("volume=0.0100", graph)
        self.assertIn("amix=", graph)
        self.assertIn("tpad=stop_mode=clone", graph)
        self.assertNotIn("-stream_loop", " ".join(command_from_plan(
            "ffmpeg", "in.mp4", "out.mp4", self._plan(options), MEDIA, options)))

    def test_photos_change_duration_and_round_trip_presets(self):
        options = ProcessOptions(photos=PhotoInsertSpec(
            enabled=True, paths=(str(self.photo_a), str(self.photo_b)),
            interval=3, interval_jitter=0, hold_ms=400, hold_jitter_ms=0, fade_ms=80,
        ))
        plan = self._plan(options, seed=9)
        self.assertIn("concat=", plan.graph)
        self.assertIn("force_original_aspect_ratio=decrease", plan.graph)
        self.assertIn("fade=t=in", plan.graph)
        self.assertEqual(plan.parameters["photo_inserts"], [3.0, 6.0, 9.0])
        self.assertAlmostEqual(plan.expected_duration, 11.2, places=2)
        self.assertTrue(plan.force_verify)
        restored = ProcessOptions.from_dict(options.to_dict())
        self.assertEqual(restored.photos.paths, options.photos.paths)
        self.assertFalse(ProcessOptions.from_dict({"crf": 20}).photos.enabled)

    def test_unchecked_photos_are_not_planned_even_if_paths_exist(self):
        options = ProcessOptions(photos=PhotoInsertSpec(enabled=False, paths=(str(self.photo_a),)))
        self.assertNotIn("concat=", self._graph(options))

    def test_version_jitter_is_reproducible(self):
        options = ProcessOptions(pip=PipSpec(
            enabled=True, path=str(self.video), opacity=0.1, opacity_jitter=0.04, position_jitter=0.02,
        ))
        source = self.root / "素材.mp4"
        source.write_bytes(b"s")
        jobs = make_jobs([source], self.root / "out", 2, 2026, Preset("t"), False, options)
        first = dedup_sample(options, jobs[0].derived_seed())
        again = dedup_sample(options, jobs[0].derived_seed())
        second = dedup_sample(options, jobs[1].derived_seed())
        self.assertEqual(first, again)
        self.assertNotEqual(first["pip_opacity"], second["pip_opacity"])
        fixed = ProcessOptions(pip=PipSpec(enabled=True, path=str(self.video), opacity=0.08,
                                           opacity_jitter=0, position_jitter=0))
        self.assertEqual(dedup_sample(fixed, 1)["pip_opacity"], dedup_sample(fixed, 99)["pip_opacity"])

    def test_photo_timeline_mapping(self):
        times = photo_insert_times(2.0, 0.5, 0)
        self.assertEqual(times, [0.5, 1.0, 1.5])
        self.assertEqual(locate_output_time(0.2, 2.0, times, 0.3)[0], "main")
        kind, payload = locate_output_time(0.6, 2.0, times, 0.3)
        self.assertEqual(kind, "photo")
        self.assertEqual(payload[0], 0)
        self.assertAlmostEqual(payload[1], 0.1, places=3)

    def _plan(self, options: ProcessOptions, seed: int = 1):
        return build_plan(options, sample_options(options, seed), MEDIA, seed=seed)

    def _graph(self, options: ProcessOptions, seed: int = 1) -> str:
        return self._plan(options, seed).graph


if __name__ == "__main__":
    unittest.main()
