"""Filter-graph tests. They do not need a display or a finished encode."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yingxu.extensions import DynamicWatermarkExtension, FilterContext, SubtitleMaskExtension
from yingxu.models import MaskSpec, Preset, ProcessOptions, RangeSpec, WatermarkSpec, make_jobs
from yingxu.processing import (
    build_plan, command_from_plan, enabled_summary, options_from_preset, path_position,
    sample_options, validate_options, widget_rect_to_relative,
)
from yingxu.store import WorkbenchStore


MEDIA = {"width": 320, "height": 180, "duration": 10.0, "fps": 25.0,
         "video": {"index": 0}, "audio_count": 1}


def plan_for(options: ProcessOptions, seed: int = 7, **kwargs) -> str:
    sampled = sample_options(options, seed)
    plan = build_plan(options, sampled, MEDIA, **kwargs)
    return plan.graph


class ProcessingTests(unittest.TestCase):
    def test_disabled_features_leave_no_graph(self):
        self.assertEqual(plan_for(ProcessOptions()), "")
        self.assertNotIn("hflip", plan_for(ProcessOptions()))
        self.assertNotIn("rotate", plan_for(ProcessOptions()))
        self.assertNotIn("select", plan_for(ProcessOptions()))

    def test_mirror_rotate_and_drop_are_opt_in(self):
        mirrored = plan_for(ProcessOptions(mirror_h=True))
        self.assertIn("hflip", mirrored)
        self.assertNotIn("vflip", mirrored)
        rotated = plan_for(ProcessOptions(rotate=RangeSpec(True, 90, 90)))
        self.assertIn("transpose=1", rotated)
        dropped = plan_for(ProcessOptions(drop_enabled=True, drop_interval=6))
        self.assertIn("select=", dropped)
        self.assertIn("setpts=", dropped)

    def test_speed_keeps_audio_with_video(self):
        graph = plan_for(ProcessOptions(speed=RangeSpec(True, 1.25, 1.25)))
        self.assertIn("setpts=PTS/1.250000", graph)
        self.assertIn("atempo=1.25000", graph)

    def test_color_ranges_are_reproducible_and_bounded(self):
        options = ProcessOptions(
            brightness=RangeSpec(True, -0.02, 0.02),
            contrast=RangeSpec(True, 0.98, 1.02),
            saturation=RangeSpec(True, 0.97, 1.03),
        )
        first = sample_options(options, 99)
        second = sample_options(options, 99)
        self.assertEqual(first, second)
        self.assertLessEqual(abs(first["brightness"]), 0.02)
        self.assertTrue(0.98 <= first["contrast"] <= 1.02)
        graph = plan_for(options, seed=99)
        self.assertIn("eq=brightness=", graph)
        plain = ProcessOptions()
        sampled = sample_options(plain, 99)
        self.assertIsNone(sampled["brightness"])

    def test_enabling_one_range_does_not_change_another_draw(self):
        base = ProcessOptions(brightness=RangeSpec(True, -0.05, 0.05))
        extra = ProcessOptions(brightness=RangeSpec(True, -0.05, 0.05), blur=RangeSpec(True, 0.2, 0.4))
        self.assertEqual(sample_options(base, 5)["brightness"], sample_options(extra, 5)["brightness"])

    def test_watermark_text_and_time_window(self):
        mark = WatermarkSpec(enabled=True, style="text", text="影序", size=28, opacity=0.4)
        graph = plan_for(ProcessOptions(watermark=mark))
        self.assertIn("drawtext=", graph)
        self.assertIn("影序", graph)
        image = plan_for(ProcessOptions(watermark=WatermarkSpec(
            enabled=True, style="image", image=str(Path("presets/只转码.json")), size=24, opacity=0.5)))
        self.assertIn("shortest=1", image)
        self.assertIn("-framerate", " ".join(command_from_plan(
            "ffmpeg", "in.mp4", "out.mp4",
            build_plan(ProcessOptions(watermark=WatermarkSpec(
                enabled=True, style="image", image=str(Path("presets/只转码.json")), size=24)),
                       sample_options(ProcessOptions(), 1), MEDIA),
            MEDIA, ProcessOptions())))
        hidden = build_plan(ProcessOptions(watermark=WatermarkSpec(
            enabled=True, style="text", text="影序", start=5, end=8)),
            sample_options(ProcessOptions(), 1), MEDIA, preview_time=1.0, include_audio=False)
        self.assertNotIn("drawtext=", hidden.graph)
        shown = build_plan(ProcessOptions(watermark=mark), sample_options(ProcessOptions(), 1),
                           MEDIA, preview_time=1.0, include_audio=False)
        self.assertIn("drawtext=", shown.graph)

    def test_mask_blur_is_limited_to_its_time_span(self):
        mask = MaskSpec(mode="blur", x=0.0, y=0.75, w=1.0, h=0.2, blur=8, start=5, end=8, feather=6)
        graph = plan_for(ProcessOptions(masks=(mask,)))
        self.assertIn("gblur", graph)
        self.assertIn("overlay=", graph)
        self.assertIn("between(t,5.000,8.000)", graph)
        outside = build_plan(ProcessOptions(masks=(mask,)), sample_options(ProcessOptions(), 1),
                             MEDIA, preview_time=1, include_audio=False)
        self.assertNotIn("gblur", outside.graph)
        inside = build_plan(ProcessOptions(masks=(mask,)), sample_options(ProcessOptions(), 1),
                            MEDIA, preview_time=6, include_audio=False)
        self.assertIn("gblur", inside.graph)
        self.assertIn("geq=", inside.graph)

    def test_copy_mask_flips_only_the_patch(self):
        mask = MaskSpec(mode="flip_h", x=0.1, y=0.7, w=0.8, h=0.2, src_x=0.1, src_y=0.45, src_w=0.8, src_h=0.2)
        graph = plan_for(ProcessOptions(masks=(mask,)))
        self.assertIn("hflip", graph)
        self.assertNotIn("[__SRC__]hflip", graph)
        self.assertLess(graph.index("crop="), graph.index("hflip"))

    def test_builtin_presets_only_enable_their_ranges(self):
        plain = options_from_preset(Preset.from_file(Path("presets/只转码.json")))
        self.assertFalse(plain.brightness.enabled)
        self.assertFalse(plain.mirror_h)
        standard = options_from_preset(Preset.from_file(Path("presets/标准处理.json")))
        self.assertTrue(standard.brightness.enabled)
        self.assertFalse(standard.drop_enabled)
        self.assertIn("亮度", " ".join(enabled_summary(standard)))

    def test_profile_roundtrip_and_validation(self):
        options = ProcessOptions(
            brightness=RangeSpec(True, -0.01, 0.01),
            watermark=WatermarkSpec(enabled=True, text="A", style="move_h", speed=40),
            masks=(MaskSpec(start=1, end=2),),
        )
        restored = ProcessOptions.from_dict(options.to_dict())
        self.assertEqual(restored.watermark.text, "A")
        self.assertEqual(restored.masks[0].end, 2)
        with self.assertRaises(ValueError):
            validate_options(ProcessOptions(speed=RangeSpec(True, 8, 9)))
        with self.assertRaises(ValueError):
            validate_options(ProcessOptions(watermark=WatermarkSpec(enabled=True, style="text", text="")))

    def test_jobs_keep_the_same_options_object(self):
        root = Path(tempfile.mkdtemp())
        source = root / "素材.mp4"
        source.write_bytes(b"x")
        options = ProcessOptions(mirror_v=True)
        preset = Preset("t")
        jobs = make_jobs([source], root / "out", 2, 3, preset, True, options)
        self.assertTrue(all(job.options is options for job in jobs))
        self.assertEqual(len(jobs), 2)

    def test_legacy_command_shape_and_complex_command(self):
        options = ProcessOptions()
        sampled = sample_options(options, 1)
        empty = build_plan(options, sampled, MEDIA)
        command = command_from_plan("ffmpeg", "in.mp4", "out.mp4", empty, MEDIA, options)
        self.assertNotIn("-filter_complex", command)
        self.assertIn("libx264", command)
        marked = ProcessOptions(watermark=WatermarkSpec(enabled=True, text="WX", opacity=1))
        plan = build_plan(marked, sample_options(marked, 1), MEDIA)
        command = command_from_plan("ffmpeg", "in.mp4", "out.mp4", plan, MEDIA, marked)
        self.assertIn("-filter_complex", command)
        self.assertIn("0:a?", command)
        self.assertTrue(plan.has_audio)
        self.assertNotIn("[aout]", plan.graph)

    def test_widget_rect_respects_letterbox(self):
        # 200x100 widget, 100x100 frame -> side bars of 50px.
        got = widget_rect_to_relative(200, 100, 100, 100, 50, 0, 150, 100)
        self.assertEqual(got, (0.0, 0.0, 1.0, 1.0))
        self.assertIsNone(widget_rect_to_relative(200, 100, 100, 100, 0, 0, 10, 4))

    def test_path_pingpong_hits_the_ends(self):
        points = ((0.0, 0.0), (1.0, 0.0))
        self.assertAlmostEqual(path_position(points, 0, 4, 100, 50)[0], 0)
        self.assertAlmostEqual(path_position(points, 2, 4, 100, 50)[0], 100)
        self.assertAlmostEqual(path_position(points, 4, 4, 100, 50)[0], 0)

    def test_extensions_refuse_empty_and_build_real_filters(self):
        ctx = FilterContext(320, 180, 10)
        with self.assertRaises(NotImplementedError):
            DynamicWatermarkExtension().build_filters(ctx)
        graph = DynamicWatermarkExtension(WatermarkSpec(enabled=True, text="WX")).build_filters(ctx)[0]
        self.assertIn("drawtext=", graph)
        with self.assertRaises(NotImplementedError):
            SubtitleMaskExtension().build_filters(ctx)
        mask_graph = SubtitleMaskExtension((MaskSpec(mode="copy"),)).build_filters(ctx)[0]
        self.assertIn("overlay=", mask_graph)

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkbenchStore(Path(tmp) / "t.db")
            store.save_preset("自定义", {"options": {"mute": True}})
            self.assertTrue(store.load_preset("自定义")["options"]["mute"])
            store.add_task("素材.mp4", 1, "success", "out.mp4", "完成", {"codec": "libx264"})
            rows = store.recent_tasks()
            self.assertEqual(rows[0]["status"], "success")
            self.assertIn("素材.mp4", rows[0]["source"])


if __name__ == "__main__":
    unittest.main()
