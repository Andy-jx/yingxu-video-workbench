"""PySide6 Windows desktop interface for the real queue and FFmpeg engine."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    QAbstractItemView, QHeaderView,
)

from .engine import resolve_executable
from .models import JobSpec, Preset, make_jobs, preset_dir
from .queue import BatchRunner, QueueEvent


STYLE = """
QWidget {background: #0d121a; color: #e5edf4; font-family: 'Microsoft YaHei UI','Segoe UI'; font-size: 13px;}
QFrame#panel {background: #151d28; border: 1px solid #273544; border-radius: 12px;}
QLabel#brand {font-size: 26px; font-weight: 700; color: #f4f7fb;}
QLabel#subtitle {color: #8fa2b6; font-size: 12px;}
QLabel#section {font-size: 15px; font-weight: 700; color: #e6f3f2;}
QLabel#hint {color: #94a8b9; font-size: 11px;}
QPushButton {background: #263342; border: 1px solid #3b4b5b; border-radius: 7px; padding: 9px 13px; font-weight: 600;}
QPushButton:hover {background: #334657;}
QPushButton:disabled {color: #657585; border-color: #293540; background: #18222c;}
QPushButton#primary {background: #2eb89c; color: #081a1d; border: none; font-size: 15px; padding: 13px;}
QPushButton#primary:hover {background: #4bceb4;}
QLineEdit, QSpinBox, QComboBox {background: #101823; border: 1px solid #334352; border-radius: 6px; padding: 8px; selection-background-color: #2d987f;}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {border-color: #41bfaa;}
QListWidget, QTableWidget, QPlainTextEdit {background: #101823; border: 1px solid #303f50; border-radius: 7px; selection-background-color: #254f54; outline: none;}
QTableWidget {gridline-color: #24303e;}
QHeaderView::section {background: #202b38; color: #afbecd; border: 0px; border-bottom: 1px solid #354656; padding: 9px;}
QProgressBar {background: #202d39; border: 0; border-radius: 5px; text-align: center; height: 15px; color: #eafcf7;}
QProgressBar::chunk {background: #2caf96; border-radius: 5px;}
QCheckBox {spacing: 8px; color: #c4d0db;}
QSplitter::handle {background: #0d121a; width: 8px; height: 8px;}
"""

STATE_LABELS = {"queued": "等待中", "running": "处理中", "paused": "已暂停",
                "retrying": "重试中", "failed": "失败", "success": "已完成",
                "cancelled": "已取消"}


class QueueThread(QThread):
    event_received = Signal(object)

    def __init__(self, jobs: list[JobSpec], ffmpeg: str, ffprobe: str) -> None:
        super().__init__()
        self.runner = BatchRunner(jobs, on_event=lambda e: self.event_received.emit(e),
                                  retries=1, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)

    def run(self) -> None:
        self.runner.run()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("影序 · 智能视频处理工作台 | V0")
        self.resize(1420, 900)
        self.setMinimumSize(1120, 730)
        self.sources: list[Path] = []
        self.tasks: dict[str, JobSpec] = {}
        self.rows: dict[str, int] = {}
        self.states: dict[str, str] = {}
        self.thread: QueueThread | None = None
        self.is_paused = False
        self.log_path = Path.home() / ".yingxu" / "logs" / f"{datetime.now():%Y-%m-%d}.log"
        self._build()
        self._load_presets()
        self.write_log("工作台已启动。先导入素材，再设置版本数并加入队列。")

    def _button(self, label: str, handler, primary: bool = False) -> QPushButton:
        btn = QPushButton(label)
        if primary:
            btn.setObjectName("primary")
        btn.clicked.connect(handler)
        return btn

    def _label(self, text: str, style: str = "") -> QLabel:
        widget = QLabel(text)
        if style:
            widget.setObjectName(style)
        widget.setWordWrap(True)
        return widget

    def _panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(17, 17, 17, 17)
        layout.setSpacing(12)
        layout.addWidget(self._label(title, "section"))
        return frame, layout

    def _build(self) -> None:
        outer = QWidget()
        self.setCentralWidget(outer)
        root = QVBoxLayout(outer)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(13)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.addWidget(self._label("影序  /  YINGXU", "brand"))
        title_block.addWidget(self._label("智能视频处理工作台  ·  本地任务队列与安全导出", "subtitle"))
        header.addLayout(title_block)
        header.addStretch()
        self.header_status = self._label("引擎待命  ·  V0", "subtitle")
        header.addWidget(self.header_status)
        root.addLayout(header)

        top_split = QSplitter(Qt.Horizontal)
        top_split.setChildrenCollapsible(False)
        root.addWidget(top_split, 1)

        source_frame, source_layout = self._panel("01  /  素材导入")
        top_split.addWidget(source_frame)
        source_layout.addWidget(self._label("支持 MP4 / MOV / MKV / AVI / WEBM 等常见视频格式", "hint"))
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        source_layout.addWidget(self.source_list, 1)
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(self._button("+ 导入视频", self.import_sources))
        source_buttons.addWidget(self._button("移除选中", self.remove_sources))
        source_layout.addLayout(source_buttons)
        source_layout.addWidget(self._label("提示：这里只导入素材，不会修改原始文件。", "hint"))

        setting_frame, setting_layout = self._panel("02  /  处理设置")
        top_split.addWidget(setting_frame)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(self._label("每条视频版本数"), 0, 0)
        self.versions = QSpinBox()
        self.versions.setRange(1, 1000)
        self.versions.setValue(5)
        grid.addWidget(self.versions, 0, 1)
        grid.addWidget(self._label("随机种子"), 1, 0)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setValue(2026)
        grid.addWidget(self.seed, 1, 1)
        grid.addWidget(self._label("参数预设"), 2, 0)
        self.preset_choice = QComboBox()
        self.preset_choice.currentIndexChanged.connect(self._preset_hint)
        grid.addWidget(self.preset_choice, 2, 1)
        setting_layout.addLayout(grid)
        self.preset_description = self._label("", "hint")
        setting_layout.addWidget(self.preset_description)

        setting_layout.addWidget(self._label("导出目录"))
        out_line = QHBoxLayout()
        self.output_edit = QLineEdit(str(Path.home() / "Videos" / "影序输出"))
        out_line.addWidget(self.output_edit, 1)
        out_line.addWidget(self._button("浏览…", self.choose_output))
        setting_layout.addLayout(out_line)
        self.full_verify = QCheckBox("完整解码校验（更安全，但耗时更长）")
        self.full_verify.setChecked(True)
        setting_layout.addWidget(self.full_verify)

        setting_layout.addWidget(self._label("FFmpeg 路径（支持 PATH 命令或完整路径）", "hint"))
        self.ffmpeg_edit = QLineEdit("ffmpeg")
        self.ffmpeg_edit.setPlaceholderText("ffmpeg 或 C:\\ffmpeg\\bin\\ffmpeg.exe")
        self.ffprobe_edit = QLineEdit("ffprobe")
        self.ffprobe_edit.setPlaceholderText("ffprobe 或 C:\\ffmpeg\\bin\\ffprobe.exe")
        setting_layout.addWidget(self.ffmpeg_edit)
        setting_layout.addWidget(self.ffprobe_edit)
        setting_layout.addWidget(self._button("检查 FFmpeg 环境", self.check_environment))
        setting_layout.addStretch()
        setting_layout.addWidget(self._button("加入批量队列", self.enqueue_sources))
        self.start_button = self._button("开始处理", self.start_queue, primary=True)
        setting_layout.addWidget(self.start_button)
        setting_layout.addWidget(self._label("动态水印 / 字幕遮罩：扩展接口已预留，V0暂未启用。", "hint"))
        top_split.setSizes([470, 580])

        bottom_split = QSplitter(Qt.Vertical)
        bottom_split.setChildrenCollapsible(False)
        root.addWidget(bottom_split, 2)

        queue_frame, queue_layout = self._panel("03  /  批量任务队列")
        bottom_split.addWidget(queue_frame)
        toolbar = QHBoxLayout()
        self.pause_btn = self._button("暂停队列", self.toggle_pause)
        toolbar.addWidget(self.pause_btn)
        toolbar.addWidget(self._button("取消选中", self.cancel_selected))
        toolbar.addWidget(self._button("取消全部", self.cancel_all))
        toolbar.addWidget(self._button("重试失败/取消项", self.retry_selected))
        toolbar.addStretch()
        self.count_label = self._label("0 项任务", "hint")
        toolbar.addWidget(self.count_label)
        queue_layout.addLayout(toolbar)
        self.task_table = QTableWidget(0, 6)
        self.task_table.setHorizontalHeaderLabels(["源视频", "版本", "状态", "进度", "输出结果", "任务ID"])
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for col in (1, 2, 3, 5):
            self.task_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.task_table.setColumnHidden(5, True)
        queue_layout.addWidget(self.task_table)

        logs_frame, logs_layout = self._panel("04  /  运行日志")
        bottom_split.addWidget(logs_frame)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(1200)
        logs_layout.addWidget(self.log_edit)
        bottom_split.setSizes([340, 150])
        self.statusBar().showMessage("V0：真实FFmpeg引擎 · 原片保护 · 任务可重试")

    def _load_presets(self) -> None:
        try:
            paths = sorted(preset_dir().glob("*.json"))
            for p in paths:
                preset = Preset.from_file(p)
                self.preset_choice.addItem(preset.name, str(p))
            if not paths:
                raise FileNotFoundError("未找到 presets/*.json")
            idx = self.preset_choice.findText("标准处理（柔和调色）")
            if idx >= 0:
                self.preset_choice.setCurrentIndex(idx)
            self._preset_hint()
        except (OSError, ValueError) as exc:
            self.show_error(f"预设加载失败：{exc}")

    def _preset_hint(self) -> None:
        path = self.preset_choice.currentData()
        if not path:
            return
        try:
            p = Preset.from_file(path)
            self.preset_description.setText(
                f"亮度 {p.brightness[0]:+.3f}～{p.brightness[1]:+.3f}  |  "
                f"对比度 {p.contrast[0]:.3f}～{p.contrast[1]:.3f}\n"
                f"饱和度 {p.saturation[0]:.3f}～{p.saturation[1]:.3f}  |  "
                f"音量 {p.audio_gain_db[0]:+.2f}～{p.audio_gain_db[1]:+.2f}dB")
        except (OSError, ValueError):
            self.preset_description.setText("预设无法读取")

    def write_log(self, message: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {message}"
        self.log_edit.appendPlainText(line)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError:
            pass

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "影序 · 提示", message)
        self.write_log(message)

    def import_sources(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频素材", str(Path.home()),
            "视频文件 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts);;所有文件 (*.*)")
        for raw in paths:
            path = Path(raw).resolve()
            if path not in self.sources:
                self.sources.append(path)
                self.source_list.addItem(str(path))
        if paths:
            self.write_log(f"导入素材：当前共 {len(self.sources)} 个文件")

    def remove_sources(self) -> None:
        for item in self.source_list.selectedItems():
            self.sources.remove(Path(item.text()))
            self.source_list.takeItem(self.source_list.row(item))

    def choose_output(self) -> None:
        current = self.output_edit.text().strip()
        chosen = QFileDialog.getExistingDirectory(self, "选择输出目录", current or str(Path.home()))
        if chosen:
            self.output_edit.setText(chosen)

    def check_environment(self) -> None:
        try:
            a = resolve_executable(self.ffmpeg_edit.text().strip(), "ffmpeg")
            b = resolve_executable(self.ffprobe_edit.text().strip(), "ffprobe")
            self.write_log(f"FFmpeg可用：{a}")
            self.write_log(f"FFprobe可用：{b}")
            QMessageBox.information(self, "环境检查", "FFmpeg 和 FFprobe 均已找到。")
        except OSError as exc:
            self.show_error(str(exc))

    def enqueue_sources(self) -> None:
        if self.thread and self.thread.isRunning():
            self.show_error("任务运行期间不能增加新队列，请等待处理结束。")
            return
        try:
            preset_path = self.preset_choice.currentData()
            if not preset_path:
                raise ValueError("请先选择有效预设")
            output = self.output_edit.text().strip()
            if not output:
                raise ValueError("请输入导出目录")
            specs = make_jobs(self.sources, Path(output), self.versions.value(),
                              self.seed.value(), Preset.from_file(preset_path), self.full_verify.isChecked())
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        for spec in specs:
            self._add_row(spec)
        self.write_log(f"已加入 {len(specs)} 项任务，当前队列 {len(self.tasks)} 项")

    def _add_row(self, spec: JobSpec) -> None:
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        self.tasks[spec.job_id] = spec
        self.rows[spec.job_id] = row
        self.states[spec.job_id] = "queued"
        for column, value in [(0, spec.source.name), (1, f"V{spec.version:03d}"),
                              (2, "等待中"), (4, "—"), (5, spec.job_id)]:
            self.task_table.setItem(row, column, QTableWidgetItem(value))
        bar = QProgressBar()
        bar.setValue(0)
        bar.setMinimumWidth(120)
        self.task_table.setCellWidget(row, 3, bar)
        self.task_table.setRowHeight(row, 41)
        self._update_count()

    def _update_count(self) -> None:
        total = len(self.tasks)
        success = sum(s == "success" for s in self.states.values())
        failed = sum(s == "failed" for s in self.states.values())
        self.count_label.setText(f"{total} 项  ·  完成 {success}  ·  失败 {failed}")

    def _selected_ids(self) -> list[str]:
        return [self.task_table.item(index.row(), 5).text()
                for index in self.task_table.selectionModel().selectedRows()]

    def start_queue(self) -> None:
        if self.thread and self.thread.isRunning():
            self.show_error("队列正在运行，请先等待或取消。")
            return
        pending = [spec for jid, spec in self.tasks.items() if self.states[jid] == "queued"]
        if not pending:
            self.show_error("当前没有等待执行的任务。请先导入视频并加入队列。")
            return
        try:
            resolve_executable(self.ffmpeg_edit.text().strip(), "ffmpeg")
            resolve_executable(self.ffprobe_edit.text().strip(), "ffprobe")
        except OSError as exc:
            self.show_error(str(exc))
            return
        self.thread = QueueThread(pending, self.ffmpeg_edit.text().strip(), self.ffprobe_edit.text().strip())
        self.thread.event_received.connect(self.on_queue_event)
        self.thread.finished.connect(self.on_queue_finished)
        self.is_paused = False
        self.pause_btn.setText("暂停队列")
        self.header_status.setText("正在处理 · 后台运行")
        self.start_button.setEnabled(False)
        self.write_log(f"队列开始：{len(pending)} 项任务")
        self.thread.start()

    def on_queue_event(self, event: QueueEvent) -> None:
        row = self.rows.get(event.job_id)
        if row is None:
            return
        self.states[event.job_id] = event.status
        self.task_table.item(row, 2).setText(STATE_LABELS.get(event.status, event.status))
        bar = self.task_table.cellWidget(row, 3)
        if isinstance(bar, QProgressBar):
            bar.setValue(event.percent)
        if event.output:
            self.task_table.item(row, 4).setText(str(event.output))
        if event.status in {"failed", "success", "cancelled", "retrying", "paused"}:
            self.write_log(f"{self.tasks[event.job_id].source.name} V{self.tasks[event.job_id].version:03d} — {event.message}")
        self._update_count()

    def on_queue_finished(self) -> None:
        self.header_status.setText("引擎待命 · 任务已结束")
        self.start_button.setEnabled(True)
        self.pause_btn.setText("暂停队列")
        self.is_paused = False
        self.write_log("本轮任务队列已结束。")
        self.thread = None

    def toggle_pause(self) -> None:
        if not self.thread or not self.thread.isRunning():
            self.show_error("当前没有运行中的任务。")
            return
        try:
            if self.is_paused:
                self.thread.runner.resume()
                self.is_paused = False
                self.pause_btn.setText("暂停队列")
                self.write_log("队列继续运行")
            else:
                self.thread.runner.pause()
                self.is_paused = True
                self.pause_btn.setText("继续队列")
                self.write_log("队列已暂停，运行中的 FFmpeg 进程也已暂停")
        except Exception as exc:
            self.show_error(f"暂停/继续失败：{exc}")

    def _cancel(self, ids: list[str]) -> None:
        if not ids:
            return
        active = [i for i in ids if self.states.get(i) in {"queued", "running", "paused", "retrying"}]
        if not active:
            return
        if self.thread and self.thread.isRunning():
            self.thread.runner.cancel(active)
        else:
            for i in active:
                self.on_queue_event(QueueEvent(i, "cancelled", 0, "用户取消"))
        self.write_log(f"已请求取消 {len(active)} 项任务")

    def cancel_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self.show_error("请在任务队列中选中要取消的项目。")
        else:
            self._cancel(ids)

    def cancel_all(self) -> None:
        self._cancel(list(self.tasks))
        if self.thread and self.thread.isRunning() and self.is_paused:
            self.thread.runner.resume()
            self.is_paused = False
            self.pause_btn.setText("暂停队列")

    def retry_selected(self) -> None:
        if self.thread and self.thread.isRunning():
            self.show_error("请等待当前队列结束后，再重试失败的任务。")
            return
        ids = self._selected_ids() or list(self.tasks)
        count = 0
        for job_id in ids:
            if self.states.get(job_id) in {"failed", "cancelled"}:
                self.on_queue_event(QueueEvent(job_id, "queued", 0, "重新排队"))
                self.task_table.item(self.rows[job_id], 4).setText("—")
                count += 1
        self.write_log(f"重新排队 {count} 项任务")

    def closeEvent(self, event) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务运行中", "请先取消全部任务并等待队列结束，再关闭窗口，避免留下临时文件。")
            event.ignore()
            return
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
