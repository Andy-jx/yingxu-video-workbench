"""Offscreen smoke test for the workbench window."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    from yingxu.gui import MainWindow
except Exception:  # pragma: no cover - engine tests stay usable without Qt
    QApplication = MainWindow = None


@unittest.skipUnless(QApplication and MainWindow, "requires PySide6")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_window_exposes_real_controls_and_queues_versions(self) -> None:
        window = MainWindow()
        try:
            self.assertEqual(window.windowTitle(), "影序 · 视频处理工作台")
            self.assertNotIn("YINGXU", window.windowTitle())
            self.assertEqual(window.start_button.text(), "开始处理")
            self.assertFalse(window.advanced.isVisible())
            self.assertFalse(window.mirror_h.isChecked())
            self.assertFalse(window.drop_enabled.isChecked())
            self.assertEqual(window.stack.count(), 6)
            self.assertFalse(window.pip_enabled.isChecked())
            self.assertFalse(window.photo_enabled.isChecked())
            window.wm_enabled.setChecked(True)
            self.assertEqual(window.preview_mode, "processed")
            self.assertTrue(window.processed_button.isChecked())
            self.assertEqual(window.wm_text.text(), "影序")
            window.wm_style.setCurrentIndex(window.wm_style.findData("image"))
            self.assertTrue(window.wm_image.text().endswith("default_logo.png"))
            self.assertGreaterEqual(window.wm_size.value(), 120)
            self.assertGreaterEqual(window.wm_opacity.value(), 0.6)
            window.wm_enabled.setChecked(False)
            self.assertGreaterEqual(window.minimumWidth(), 960)
            self.assertGreaterEqual(window.minimumHeight(), 640)

            index = window.preset_choice.findText("只转码（无调色）")
            window.preset_choice.setCurrentIndex(index)
            self.assertFalse(window.brightness.box.isChecked())
            self.assertIn("编码", window.summary_label.text())

            for button in window.mode_group.buttons():
                if button.property("mode") == "film":
                    button.setChecked(True)
            self.assertEqual(window.mode, "film")
            self.assertFalse(window.mirror_h.isEnabled())
            self.assertFalse(window.rotate.isEnabled())
            self.assertFalse(window.drop_enabled.isChecked())
            self.assertFalse(window.photo_box.isEnabled())
            self.assertTrue(window.pip_enabled.isEnabled())

            root = Path(tempfile.mkdtemp())
            source = root / "素材.mp4"
            source.write_bytes(b"placeholder")
            window.output_edit.setText(str(root / "导出"))
            window.versions.setValue(2)
            window.mode_group.buttons()[0].setChecked(True)
            window._add_paths([str(source)])
            window.enqueue_sources()
            self.assertEqual(window.task_table.rowCount(), 2)
            self.assertEqual(window.task_table.item(0, 2).text(), "等待中")
        finally:
            self._stop(window)
            window.close()

    def test_later_mask_updates_a_saved_clip_and_end_zero_is_kept(self) -> None:
        window = MainWindow()
        try:
            root = Path(tempfile.mkdtemp())
            source = root / "素材.mp4"
            source.write_bytes(b"placeholder")
            window.output_edit.setText(str(root / "导出"))
            window._add_paths([str(source)])
            window.save_override()
            self.assertEqual(window._options_for(source.resolve()).masks, ())
            window._applying_mask = True
            window.mask_x.setValue(8)
            window.mask_y.setValue(72)
            window.mask_w.setValue(84)
            window.mask_h.setValue(16)
            window.mask_end.setValue(0)
            window._applying_mask = False
            window._save_box_as_mask()
            masks = window._options_for(source.resolve()).masks
            self.assertEqual(len(masks), 1)
            self.assertEqual(masks[0].end, 0)
            self.assertIn("蒙版1段", window.source_list.item(0).text())
            window.mask_blur.setValue(9)
            self.assertEqual(window._options_for(source.resolve()).masks[0].blur, 9)
        finally:
            window.close()

    def test_start_enqueues_current_videos_when_the_queue_is_empty(self) -> None:
        window = MainWindow()
        try:
            root = Path(tempfile.mkdtemp())
            source = root / "素材.mp4"
            source.write_bytes(b"placeholder")
            window.output_edit.setText(str(root / "导出"))
            window.versions.setValue(1)
            window._add_paths([str(source)])
            self.assertEqual(window.task_table.rowCount(), 0)
            window.start_queue()
            self.assertEqual(window.task_table.rowCount(), 1)
            self.assertEqual(window.task_table.item(0, 0).text(), "素材.mp4")
        finally:
            self._stop(window)
            window.close()

    def _stop(self, window: MainWindow) -> None:
        thread = window.thread
        if thread and thread.isRunning():
            thread.runner.cancel(list(window.tasks))
            thread.wait(5000)


if __name__ == "__main__":
    unittest.main()
