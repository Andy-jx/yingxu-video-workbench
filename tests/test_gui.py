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
            self.assertEqual(window.stack.count(), 5)
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
            window.close()


if __name__ == "__main__":
    unittest.main()
