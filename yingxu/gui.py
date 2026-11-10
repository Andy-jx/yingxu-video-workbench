"""PySide6 workbench for the local FFmpeg queue."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QDoubleSpinBox, QSplitter, QStackedWidget,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .engine import probe_media, resolve_executable
from .models import (
    ENCODER_PRESETS, JobSpec, MaskSpec, PhotoInsertSpec, PipSpec, Preset, ProcessOptions,
    RangeSpec, WatermarkSpec, make_jobs, preset_dir,
)
from .processing import (
    adjacent_source, apply_default_logo, default_logo_path, enabled_summary, extract_frame,
    find_cjk_font, list_video_encoders, load_profile, options_from_preset, prepare_preview_options,
    render_clip, render_still, risky_warnings, validate_against_media, validate_options,
    visual_effects, widget_rect_to_relative,
)
from .queue import BatchRunner, QueueEvent
from .store import WorkbenchStore

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
except Exception:  # pragma: no cover - optional playback backend
    QAudioOutput = QMediaPlayer = QVideoWidget = None


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts"}
STYLE_LABELS = [
    ("固定文字", "text"), ("图片 LOGO", "image"), ("半透明", "translucent"),
    ("全屏平铺", "tile"), ("斜向排列", "diagonal"), ("横向移动", "move_h"),
    ("纵向移动", "move_v"), ("自定义轨迹", "path"),
]
MASK_LABELS = [("模糊遮盖", "blur"), ("局部复制", "copy"), ("左右翻转覆盖", "flip_h"), ("上下翻转覆盖", "flip_v")]
STATE_LABELS = {
    "queued": "等待中", "running": "处理中", "paused": "已暂停", "retrying": "重试中",
    "failed": "失败", "success": "已完成", "cancelled": "已取消",
}

STYLE = """
QWidget { background: #1c1e22; color: #e7e9ee; font-family: "Microsoft YaHei UI", "Segoe UI", "WenQuanYi Micro Hei"; font-size: 13px; }
QMainWindow, QStatusBar { background: #16181c; }
QFrame#panel { background: #252830; border: 1px solid #3c414b; border-radius: 6px; }
QLabel#title { font-size: 20px; font-weight: 700; color: #f4f6f8; }
QLabel#muted, QLabel#hint { color: #a7afbb; }
QLabel#warning { color: #e3c07a; }
QPushButton { background: #323844; border: 1px solid #4a5160; border-radius: 4px; padding: 6px 12px; min-height: 28px; }
QPushButton:hover { background: #3d4554; }
QPushButton:disabled { color: #7d8694; background: #2a2e36; border-color: #3a404a; }
QPushButton#primary { background: #2f6fed; color: white; border: none; font-weight: 700; min-height: 36px; min-width: 120px; }
QPushButton#primary:hover { background: #4b84f5; }
QPushButton#primary:disabled { background: #2a3d66; color: #c5d0e4; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QListWidget, QTableWidget {
    background: #171a1f; border: 1px solid #434a57; border-radius: 4px; padding: 4px 6px;
    selection-background-color: #2a4f8f;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #5b8def; }
QComboBox QAbstractItemView { background: #23272f; color: #e7e9ee; selection-background-color: #2f6fed; }
QHeaderView::section { background: #2c313a; color: #c5ccd6; border: none; border-bottom: 1px solid #3c414b; padding: 6px; }
QTableWidget { gridline-color: #343a44; alternate-background-color: #22262d; }
QProgressBar { background: #2a3038; border: none; border-radius: 3px; text-align: center; color: #e7e9ee; min-height: 14px; }
QProgressBar::chunk { background: #2f6fed; border-radius: 3px; }
QListWidget::item { padding: 6px 8px; }
QListWidget::item:selected { background: #2a4a86; }
QGroupBox { border: 1px solid #3c414b; border-radius: 4px; margin-top: 12px; padding: 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #d5dbe3; }
QRadioButton, QCheckBox { spacing: 6px; }
QSplitter::handle { background: #16181c; }
QScrollArea { border: none; background: #1c1e22; }
QStatusBar { color: #b7bec8; }
QPushButton:checked { background: #2a4a86; border-color: #5b8def; color: #ffffff; }
QLabel#section { color: #8f9aab; font-size: 12px; font-weight: 700; padding-top: 4px; }
QLabel#paneTitle { color: #c5ccd6; font-size: 12px; padding: 2px 4px; background: #121418; }
QLabel#timecode { color: #d5dbe3; font-family: Consolas, "Microsoft YaHei UI"; min-width: 120px; }
QFrame#stage { background: #0e1013; border: 1px solid #2c313a; border-radius: 4px; }
QTabWidget::pane { border: 1px solid #3c414b; border-radius: 6px; background: #252830; top: -1px; }
QTabBar::tab { background: #23272f; color: #aab3c0; padding: 6px 16px; border: 1px solid #3c414b; border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
QTabBar::tab:selected { background: #252830; color: #f4f6f8; }
QSlider::groove:horizontal { height: 4px; background: #3a404b; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #2f6fed; border-radius: 2px; }
QSlider::handle:horizontal { background: #e7e9ee; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }
"""


def user_preset_dir() -> Path:
    path = Path.home() / ".yingxu" / "presets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


class QueueThread(QThread):
    event_received = Signal(object)

    def __init__(self, jobs: list[JobSpec], ffmpeg: str, ffprobe: str) -> None:
        super().__init__()
        self.runner = BatchRunner(jobs, on_event=lambda event: self.event_received.emit(event),
                                  retries=1, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)

    def run(self) -> None:
        self.runner.run()


class ProbeThread(QThread):
    probed = Signal(str, object, str)

    def __init__(self, path: str, ffprobe: str) -> None:
        super().__init__()
        self.path = path
        self.ffprobe = ffprobe

    def run(self) -> None:
        try:
            info = probe_media(Path(self.path), resolve_executable(self.ffprobe, "ffprobe"))
            self.probed.emit(self.path, info, "")
        except Exception as exc:
            self.probed.emit(self.path, None, str(exc))


class PreviewThread(QThread):
    ok = Signal(int, str)
    fail = Signal(int, str)

    def __init__(self, token: int, mode: str, source: Path, media: dict, options: ProcessOptions,
                 time_sec: float, output: Path, ffmpeg: str, seed: int) -> None:
        super().__init__()
        self.token = token
        self.mode = mode
        self.source = source
        self.media = media
        self.options = options
        self.time_sec = time_sec
        self.output = output
        self.ffmpeg = ffmpeg
        self.seed = seed

    def run(self) -> None:
        try:
            if self.mode == "processed":
                render_still(self.ffmpeg, self.source, self.media, self.options, self.time_sec, self.output, self.seed)
            else:
                extract_frame(self.ffmpeg, self.source, self.time_sec, self.output)
            self.ok.emit(self.token, str(self.output))
        except Exception as exc:
            self.fail.emit(self.token, str(exc))


class ClipThread(QThread):
    ok = Signal(str)
    fail = Signal(str)

    def __init__(self, source: Path, media: dict, options: ProcessOptions, start: float,
                 length: float, output: Path, ffmpeg: str, seed: int) -> None:
        super().__init__()
        self.args = (source, media, options, start, length, output, ffmpeg, seed)

    def run(self) -> None:
        source, media, options, start, length, output, ffmpeg, seed = self.args
        try:
            render_clip(ffmpeg, source, media, options, start, length, output, seed)
            self.ok.emit(str(output))
        except Exception as exc:
            self.fail.emit(str(exc))


class SourceList(QListWidget):
    filesDropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()


class RangeRow(QWidget):
    changed = Signal()

    def __init__(self, title: str, low: float, high: float, step: float, decimals: int,
                 start: float, end: float) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.box = QCheckBox(title)
        self.box.setFixedWidth(108)
        self.low = self._spin(low, high, step, decimals, start)
        self.high = self._spin(low, high, step, decimals, end)
        layout.addWidget(self.box)
        layout.addWidget(self.low, 1)
        layout.addWidget(QLabel("～"))
        layout.addWidget(self.high, 1)
        self.box.toggled.connect(lambda *_: self.changed.emit())
        self.low.valueChanged.connect(lambda *_: self.changed.emit())
        self.high.valueChanged.connect(lambda *_: self.changed.emit())

    def _spin(self, low: float, high: float, step: float, decimals: int, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(value)
        return spin

    def spec(self) -> RangeSpec:
        return RangeSpec(self.box.isChecked(), self.low.value(), self.high.value())

    def set_spec(self, spec: RangeSpec) -> None:
        self.box.setChecked(spec.enabled)
        self.low.setValue(spec.low)
        self.high.setValue(spec.high)

    def uncheck(self) -> None:
        self.box.setChecked(False)


class PreviewCanvas(QWidget):
    regionSelected = Signal(float, float, float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(200)
        self.pixmap = QPixmap()
        self.frame_size = (0, 0)
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

    def set_frame(self, path: str, width: int, height: int) -> None:
        self.pixmap = QPixmap(path)
        self.frame_size = (width or self.pixmap.width(), height or self.pixmap.height())
        self._origin = self._current = None
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#121418"))
        if self.pixmap.isNull() or self.frame_size[0] < 2:
            painter.setPen(QColor("#9aa3b2"))
            painter.drawText(self.rect(), Qt.AlignCenter, "导入视频后在这里预览\n在字幕蒙版页拖动鼠标可框选区域")
            return
        rect = self._target()
        painter.drawPixmap(rect, self.pixmap)
        if self._origin and self._current:
            painter.setPen(QPen(QColor("#4c8dff"), 2))
            painter.setBrush(QColor(76, 141, 255, 50))
            painter.drawRect(QRect(self._origin, self._current).normalized())

    def _target(self) -> QRect:
        width, height = self.frame_size
        scale = min(self.width() / width, self.height() / height)
        box_w, box_h = max(1, int(width * scale)), max(1, int(height * scale))
        return QRect((self.width() - box_w) // 2, (self.height() - box_h) // 2, box_w, box_h)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self.pixmap.isNull():
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._origin is None or self._current is None:
            return
        rect = widget_rect_to_relative(
            self.width(), self.height(), self.frame_size[0], self.frame_size[1],
            self._origin.x(), self._origin.y(), self._current.x(), self._current.y(),
        )
        self._origin = self._current = None
        self.update()
        if rect:
            self.regionSelected.emit(*rect)


class VideoPane(QFrame):
    """One real QMediaPlayer + QVideoWidget. Seeks requested before loading are applied once loaded."""

    def __init__(self, title: str, with_audio: bool = True) -> None:
        super().__init__()
        self.setObjectName("stage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title = QLabel(title)
        self.title.setObjectName("paneTitle")
        layout.addWidget(self.title)
        if QVideoWidget is not None:
            self.view = QVideoWidget()
        else:
            self.view = QLabel("当前环境缺少 QtMultimedia，无法播放视频")
            self.view.setAlignment(Qt.AlignCenter)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.view.setMinimumHeight(120)
        layout.addWidget(self.view, 1)
        self.with_audio = with_audio
        self.player = None
        self.audio = None
        self.path = ""
        self._pending: int | None = None
        self._volume = 0.8

    @staticmethod
    def available() -> bool:
        return QMediaPlayer is not None and QVideoWidget is not None

    def _ensure(self):
        if self.player is None and self.available():
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.audio.setVolume(self._volume)
            self.audio.setMuted(not self.with_audio)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(self.view)
            self.player.mediaStatusChanged.connect(self._on_status)
        return self.player

    def load(self, path: str, start_ms: int = 0) -> bool:
        player = self._ensure()
        if player is None:
            return False
        if path != self.path:
            self.path = path
            self._pending = max(0, int(start_ms))
            player.setSource(QUrl.fromLocalFile(path))
        else:
            self.set_position(start_ms)
        return True

    def _on_status(self, status) -> None:
        loaded = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia,
                  QMediaPlayer.MediaStatus.BufferingMedia)
        if self._pending is not None and self.player is not None and status in loaded:
            self.player.setPosition(self._pending)
            self._pending = None

    def set_position(self, ms: int) -> None:
        if self.player is None:
            return
        if self._pending is not None:
            self._pending = max(0, int(ms))
        else:
            self.player.setPosition(max(0, int(ms)))

    def position(self) -> int:
        if self.player is None:
            return 0
        return self._pending if self._pending is not None else int(self.player.position())

    def play(self) -> None:
        if self.player is not None:
            self.player.play()

    def pause(self) -> None:
        if self.player is not None:
            self.player.pause()

    def is_playing(self) -> bool:
        return self.player is not None and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def at_end(self) -> bool:
        return self.player is not None and self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia

    def set_volume(self, value: float) -> None:
        self._volume = value
        if self.audio is not None:
            self.audio.setVolume(value)

    def release(self) -> None:
        """Stop and close the file so Windows can overwrite or delete it."""
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        self.path = ""
        self._pending = None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("影序 · 视频处理工作台")
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1366, 728)
        self.resize(min(1680, int(area.width() * 0.92)), min(1000, int(area.height() * 0.92)))
        self.setMinimumSize(1000, 640)
        self._compact = area.height() < 900
        self.setAcceptDrops(True)
        self.sources: list[Path] = []
        self.media: dict[str, dict] = {}
        self.overrides: dict[str, dict] = {}
        self.tasks: dict[str, JobSpec] = {}
        self.rows: dict[str, int] = {}
        self.states: dict[str, str] = {}
        self.results: dict[str, dict] = {}
        self.masks: list[MaskSpec] = []
        self.photo_paths: list[str] = []
        self._mask_preview = False
        self._last_live = 0.0
        self.thread: QueueThread | None = None
        self.preview_thread: PreviewThread | None = None
        self.clip_thread: ClipThread | None = None
        self.probe_thread: ProbeThread | None = None
        self.probe_queue: list[str] = []
        self.is_paused = False
        self.mode = "single"
        self.preview_mode = "original"
        self.playhead = 0.0
        self._loading = False
        self._token = 0
        self._preview_wanted = False
        self._playing = False
        self.view_mode = "still"  # still | play | compare
        self.effect_clip: dict | None = None
        self.compare: dict | None = None
        self._clip_serial = 0
        self._clip_then = "compare"
        self._single_base = 0
        self.store: WorkbenchStore | None = None
        self.log_path = Path.home() / ".yingxu" / "logs" / f"{datetime.now():%Y-%m-%d}.log"
        self.preview_dir = Path.home() / ".yingxu" / "preview"
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self._build()
        try:
            self.store = WorkbenchStore()
        except Exception as exc:
            self.write_log(f"任务记录不可用：{exc}")
        self._load_presets()
        self._check_env_silent()
        self.write_log("工作台已启动。导入视频，设置版本数、预设和导出目录，加入队列后开始处理。")

    def _build(self) -> None:
        outer = QWidget()
        self.setCentralWidget(outer)
        root = QVBoxLayout(outer)
        root.setContentsMargins(10, 6, 10, 4)
        root.setSpacing(6)
        root.addLayout(self._header())
        root.addWidget(self._action_bar())
        self.body = QSplitter(Qt.Horizontal)
        self.body.setChildrenCollapsible(False)
        self.body.setHandleWidth(6)
        self.body.addWidget(self._left_panel())
        self.body.addWidget(self._center_panel())
        self.body.addWidget(self._right_panel())
        self.body.setStretchFactor(0, 0)
        self.body.setStretchFactor(1, 1)
        self.body.setStretchFactor(2, 0)
        self.body.setSizes([200, 860, 380])
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.addTab(self._queue_panel(), "任务队列")
        self.bottom_tabs.addTab(self._log_panel(), "运行日志")
        self.bottom_tabs.setMinimumHeight(0)
        self.main_split = QSplitter(Qt.Vertical)
        self.main_split.setHandleWidth(6)
        self.main_split.addWidget(self.body)
        self.main_split.addWidget(self.bottom_tabs)
        self.main_split.setCollapsible(0, False)
        self.main_split.setCollapsible(1, True)
        self.main_split.setStretchFactor(0, 1)
        self.main_split.setStretchFactor(1, 0)
        self._bottom_size = 170 if self._compact else 250
        self.main_split.setSizes([900, self._bottom_size])
        self.main_split.splitterMoved.connect(lambda *_: self._sync_bottom_button())
        self._bottom_user = False
        self._layout_compact = None
        root.addWidget(self.main_split, 1)
        self.statusBar().showMessage("就绪 · 不覆盖原片")
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._request_preview)
        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(120)
        self.ui_timer.timeout.connect(self._tick_playback)
        self._set_mode("single")

    def _header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)
        title = QLabel("影序 · 视频处理工作台")
        title.setObjectName("title")
        flow = QLabel("导入视频 → 设版本数、预设和导出目录 → 加入队列 → 开始处理")
        flow.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(flow)
        layout.addStretch()
        self.env_label = QLabel("正在检查 FFmpeg")
        self.env_label.setObjectName("muted")
        layout.addWidget(self.env_label)
        self.bottom_button = self._button("收起队列/日志", self.toggle_bottom)
        layout.addWidget(self.bottom_button)
        return layout

    def toggle_bottom(self) -> None:
        self._bottom_user = True
        sizes = self.main_split.sizes()
        if len(sizes) == 2 and sizes[1] > 8:
            self._bottom_size = sizes[1]
            self.main_split.setSizes([sizes[0] + sizes[1], 0])
        else:
            total = sum(sizes) or 900
            keep = max(140, self._bottom_size)
            self.main_split.setSizes([max(200, total - keep), keep])
        self._sync_bottom_button()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        compact = self.height() < 860
        if compact == self._layout_compact:
            return
        self._layout_compact = compact
        self.source_list.setFixedHeight(52 if compact else 72)
        if self._bottom_user:
            return
        sizes = self.main_split.sizes()
        total = sum(sizes) or 900
        if compact:
            if len(sizes) == 2 and sizes[1] > 8:
                self._bottom_size = max(140, sizes[1])
            self.main_split.setSizes([total, 0])
        else:
            keep = max(200, self._bottom_size)
            self.main_split.setSizes([max(200, total - keep), keep])
        self._sync_bottom_button()

    def show_bottom(self, tab: int = 0) -> None:
        sizes = self.main_split.sizes()
        if len(sizes) == 2 and sizes[1] <= 8:
            self.toggle_bottom()
        self.bottom_tabs.setCurrentIndex(tab)

    def _sync_bottom_button(self) -> None:
        sizes = self.main_split.sizes()
        hidden = len(sizes) == 2 and sizes[1] <= 8
        self.bottom_button.setText("展开队列/日志" if hidden else "收起队列/日志")

    def _action_bar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        grid = QGridLayout(frame)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        self.versions = self._int_spin(1, 1000, 5)
        self.seed = self._int_spin(0, 2147483647, 2026)
        self.preset_choice = QComboBox()
        self.preset_choice.setMinimumWidth(180)
        self.preset_choice.currentIndexChanged.connect(self._preset_changed)
        self.full_verify = QCheckBox("完整解码校验")
        self.full_verify.setChecked(True)
        self.full_verify.toggled.connect(self._on_form_changed)
        enqueue = self._button("加入队列", self.enqueue_sources)
        self.start_button = self._button("开始处理", self.start_queue, primary=True)
        grid.addWidget(QLabel("版本数"), 0, 0)
        grid.addWidget(self.versions, 0, 1)
        grid.addWidget(QLabel("种子"), 0, 2)
        grid.addWidget(self.seed, 0, 3)
        grid.addWidget(self._button("换一个", self._roll_seed), 0, 4)
        grid.addWidget(QLabel("预设"), 0, 5)
        grid.addWidget(self.preset_choice, 0, 6)
        grid.addWidget(self.full_verify, 0, 7)
        grid.addWidget(enqueue, 0, 8)
        grid.addWidget(self.start_button, 0, 9)
        grid.addWidget(QLabel("导出目录"), 1, 0)
        self.output_edit = QLineEdit(str(Path.home() / "Videos" / "影序输出"))
        grid.addWidget(self.output_edit, 1, 1, 1, 7)
        grid.addWidget(self._button("浏览…", self.choose_output), 1, 8, 1, 2)
        self.versions.valueChanged.connect(self._on_form_changed)
        self.seed.valueChanged.connect(self._on_form_changed)
        self.output_edit.textChanged.connect(self._on_form_changed)
        return frame

    def _left_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        frame.setMinimumWidth(180)
        frame.setMaximumWidth(260)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.addWidget(self._section("工作模式"))
        self.mode_group = QButtonGroup(self)
        for mode, text in (("single", "单视频多版本"), ("batch", "批量素材"), ("film", "成片二次处理")):
            button = QRadioButton(text)
            button.setProperty("mode", mode)
            self.mode_group.addButton(button)
            layout.addWidget(button)
        self.mode_group.buttons()[0].setChecked(True)
        self.mode_group.buttonToggled.connect(lambda button, checked: self._set_mode(button.property("mode")) if checked else None)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("hint")
        self.mode_hint.setWordWrap(True)
        layout.addWidget(self.mode_hint)
        layout.addSpacing(4)
        layout.addWidget(self._section("功能"))
        self.nav = QListWidget()
        for name in ("画面", "音频", "水印", "字幕蒙版", "叠加加帧", "导出"):
            self.nav.addItem(name)
        self.nav.setCurrentRow(0)
        row_h = max(30, self.nav.sizeHintForRow(0))
        self.nav.setFixedHeight(row_h * self.nav.count() + 2 * self.nav.frameWidth() + 6)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.currentRowChanged.connect(self._page_changed)
        layout.addWidget(self.nav)
        layout.addSpacing(4)
        layout.addWidget(self._section("本批次启用"))
        self.summary_label = QLabel()
        self.summary_label.setObjectName("hint")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        summary_scroll = QScrollArea()
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        summary_scroll.setWidget(self.summary_label)
        summary_scroll.setMinimumHeight(40)
        layout.addWidget(summary_scroll, 1)
        return frame

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    def _center_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.source_list = SourceList()
        self.source_list.setFixedHeight(72)
        self.source_list.setToolTip("支持 MP4、MOV、MKV、AVI、WEBM、M4V、TS，可拖入文件或文件夹。导入不会改原片。")
        self.source_list.filesDropped.connect(self._add_paths)
        self.source_list.currentRowChanged.connect(self._on_source_changed)
        top.addWidget(self.source_list, 1)
        file_buttons = QGridLayout()
        file_buttons.setSpacing(4)
        file_buttons.addWidget(self._button("导入视频", self.import_sources), 0, 0)
        file_buttons.addWidget(self._button("移除选中", self.remove_sources), 0, 1)
        file_buttons.addWidget(self._button("清空", self.clear_sources), 0, 2)
        file_buttons.addWidget(self._button("设为单独参数", self.save_override), 1, 0, 1, 2)
        file_buttons.addWidget(self._button("清除单独参数", self.clear_override), 1, 2)
        top.addLayout(file_buttons)
        layout.addLayout(top)

        self.canvas = PreviewCanvas()
        self.canvas.setMinimumHeight(160)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.regionSelected.connect(self._on_region)
        self.single_pane = VideoPane("原片播放")
        self.video_widget = self.single_pane.view
        self.cmp_left = VideoPane("原片", with_audio=False)
        self.cmp_right = VideoPane("处理后", with_audio=True)
        compare = QWidget()
        compare_layout = QHBoxLayout(compare)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        compare_layout.setSpacing(6)
        compare_layout.addWidget(self.cmp_left, 1)
        compare_layout.addWidget(self.cmp_right, 1)
        self.compare_widget = compare
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self.canvas)
        self.preview_stack.addWidget(self.single_pane)
        self.preview_stack.addWidget(self.compare_widget)
        layout.addWidget(self.preview_stack, 1)

        transport = QHBoxLayout()
        transport.setSpacing(6)
        self.play_button = self._button("播放", self.toggle_play)
        self.play_button.setMinimumWidth(72)
        transport.addWidget(self.play_button)
        transport.addWidget(self._button("上一帧", lambda: self._step_frame(-1)))
        transport.addWidget(self._button("下一帧", lambda: self._step_frame(1)))
        self.time_label = QLabel("00:00.00 / 00:00.00")
        self.time_label.setObjectName("timecode")
        transport.addWidget(self.time_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._seek)
        transport.addWidget(self.slider, 1)
        transport.addWidget(QLabel("音量"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._set_volume)
        transport.addWidget(self.volume_slider)
        layout.addLayout(transport)

        views = QHBoxLayout()
        views.setSpacing(6)
        self.original_button = self._button("原片", lambda: self._set_preview_mode("original"))
        self.processed_button = self._button("处理后", lambda: self._set_preview_mode("processed"))
        self.view_group = QButtonGroup(self)
        for button in (self.original_button, self.processed_button):
            button.setCheckable(True)
            self.view_group.addButton(button)
        self.original_button.setChecked(True)
        views.addWidget(self.original_button)
        views.addWidget(self.processed_button)
        self.clip_button = self._button("生成 3 秒效果 · 并排对比", self.preview_clip)
        self.clip_button.setToolTip("从当前播放位置渲染 3 秒处理后片段，左右并排与原片同步播放")
        views.addWidget(self.clip_button)
        self.export_compare_button = self._button("对比导出版本…", self.compare_export)
        self.export_compare_button.setToolTip("选中队列里已完成的任务，或选择一个导出的文件，与原片左右并排同步播放")
        views.addWidget(self.export_compare_button)
        self.preview_info = QLabel("还没有画面")
        self.preview_info.setObjectName("hint")
        self.preview_info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        views.addWidget(self.preview_info, 1)
        layout.addLayout(views)
        return frame

    def _right_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_picture())
        self.stack.addWidget(self._page_audio())
        self.stack.addWidget(self._page_watermark())
        self.stack.addWidget(self._page_mask())
        self.stack.addWidget(self._page_dedup())
        self.stack.addWidget(self._page_export())
        scroll.setWidget(self.stack)
        layout.addWidget(scroll)
        return frame

    def _page_picture(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        note = QLabel("镜像、旋转、抽帧默认关闭。未勾选的项目不会执行。正角度为顺时针。")
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        color = QGroupBox("调色")
        color_layout = QVBoxLayout(color)
        self.brightness = self._range(color_layout, "亮度", -0.25, 0.25, 0.005, 3, 0, 0)
        self.contrast = self._range(color_layout, "对比度", 0.5, 2, 0.01, 3, 1, 1)
        self.saturation = self._range(color_layout, "饱和度", 0, 3, 0.01, 3, 1, 1)
        self.color_temp = self._range(color_layout, "色温", -1, 1, 0.05, 2, 0, 0)
        self.sharpen = self._range(color_layout, "锐化", 0, 2, 0.05, 2, 0, 0)
        self.blur = self._range(color_layout, "模糊", 0, 30, 0.1, 2, 0, 0)
        temp_hint = QLabel("色温：负值偏冷，正值偏暖。")
        temp_hint.setObjectName("hint")
        color_layout.addWidget(temp_hint)
        layout.addWidget(color)
        geometry = QGroupBox("几何与时间")
        geo = QVBoxLayout(geometry)
        self.scale = self._range(geo, "缩放", 0.5, 2, 0.01, 3, 1, 1)
        self.rotate = self._range(geo, "旋转", -180, 180, 1, 1, 0, 0)
        self.speed = self._range(geo, "变速", 0.25, 4, 0.01, 3, 1, 1)
        mirrors = QHBoxLayout()
        self.mirror_h = QCheckBox("水平镜像")
        self.mirror_v = QCheckBox("垂直镜像")
        self.mirror_h.toggled.connect(self._on_form_changed)
        self.mirror_v.toggled.connect(self._on_form_changed)
        mirrors.addWidget(self.mirror_h)
        mirrors.addWidget(self.mirror_v)
        geo.addLayout(mirrors)
        self.crop_enabled = QCheckBox("裁剪（像素，勾选后可在预览里框选）")
        self.crop_enabled.toggled.connect(self._on_form_changed)
        geo.addWidget(self.crop_enabled)
        crop_row = QHBoxLayout()
        self.crop_x = self._int_spin(0, 8000, 0)
        self.crop_y = self._int_spin(0, 8000, 0)
        self.crop_w = self._int_spin(0, 8000, 0)
        self.crop_h = self._int_spin(0, 8000, 0)
        for label, spin in (("X", self.crop_x), ("Y", self.crop_y), ("宽", self.crop_w), ("高", self.crop_h)):
            crop_row.addWidget(QLabel(label))
            crop_row.addWidget(spin)
            spin.valueChanged.connect(self._on_form_changed)
        geo.addLayout(crop_row)
        drop_row = QHBoxLayout()
        self.drop_enabled = QCheckBox("抽帧")
        self.drop_interval = self._int_spin(2, 30, 8)
        self.drop_enabled.toggled.connect(self._on_form_changed)
        self.drop_interval.valueChanged.connect(self._on_form_changed)
        drop_row.addWidget(self.drop_enabled)
        drop_row.addWidget(QLabel("每"))
        drop_row.addWidget(self.drop_interval)
        drop_row.addWidget(QLabel("帧删除 1 帧"))
        drop_row.addStretch()
        geo.addLayout(drop_row)
        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("输出帧率"))
        self.fps = QComboBox()
        for label, value in (("保持原帧率", 0), ("24", 24), ("25", 25), ("30", 30), ("50", 50), ("60", 60)):
            self.fps.addItem(label, value)
        self.fps.currentIndexChanged.connect(self._on_form_changed)
        fps_row.addWidget(self.fps, 1)
        geo.addLayout(fps_row)
        layout.addWidget(geometry)
        self.picture_warning = QLabel("变速会同步音频；抽帧会缩短视频。请先看预览。")
        self.picture_warning.setObjectName("warning")
        self.picture_warning.setWordWrap(True)
        layout.addWidget(self.picture_warning)
        layout.addStretch()
        return page

    def _page_audio(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        note = QLabel("视频变速或抽帧时，音轨会用 atempo 按同一倍率处理，尽量保持音调。只处理第一条音轨。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)
        self.volume = self._range(layout, "音量", -18, 18, 0.1, 2, 0, 0)
        self.denoise = QCheckBox("基础降噪")
        self.denoise.toggled.connect(self._on_form_changed)
        layout.addWidget(self.denoise)
        denoise_row = QHBoxLayout()
        denoise_row.addWidget(QLabel("强度"))
        self.denoise_nr = self._double_spin(0, 40, 1, 1, 12)
        denoise_row.addWidget(self.denoise_nr, 1)
        layout.addLayout(denoise_row)
        self.eq_enabled = QCheckBox("三段均衡")
        self.eq_enabled.toggled.connect(self._on_form_changed)
        layout.addWidget(self.eq_enabled)
        eq = QGridLayout()
        self.eq_low = self._double_spin(-12, 12, 0.5, 1, 0)
        self.eq_mid = self._double_spin(-12, 12, 0.5, 1, 0)
        self.eq_high = self._double_spin(-12, 12, 0.5, 1, 0)
        for column, (label, spin) in enumerate((("低频", self.eq_low), ("中频", self.eq_mid), ("高频", self.eq_high))):
            eq.addWidget(QLabel(label), 0, column)
            eq.addWidget(spin, 1, column)
        layout.addLayout(eq)
        self.loudnorm = QCheckBox("响度标准化（约 -16 LUFS）")
        self.loudnorm.toggled.connect(self._on_form_changed)
        self.mute = QCheckBox("原声静音")
        self.mute.toggled.connect(self._on_form_changed)
        layout.addWidget(self.loudnorm)
        layout.addWidget(self.mute)
        layout.addWidget(QLabel("背景音乐"))
        self.bgm_mode = QComboBox()
        self.bgm_mode.addItem("不使用", "off")
        self.bgm_mode.addItem("与原声混合", "mix")
        self.bgm_mode.addItem("替换原声", "replace")
        self.bgm_mode.currentIndexChanged.connect(self._on_form_changed)
        layout.addWidget(self.bgm_mode)
        bgm_row = QHBoxLayout()
        self.bgm_path = QLineEdit()
        self.bgm_path.setPlaceholderText("选择音频或带音轨的视频")
        bgm_row.addWidget(self.bgm_path, 1)
        bgm_row.addWidget(self._button("浏览…", self.choose_bgm))
        layout.addLayout(bgm_row)
        vol = QHBoxLayout()
        vol.addWidget(QLabel("BGM 音量"))
        self.bgm_volume = self._double_spin(0, 2, 0.05, 2, 0.4)
        vol.addWidget(self.bgm_volume, 1)
        layout.addLayout(vol)
        self.bgm_path.textChanged.connect(self._on_form_changed)
        layout.addStretch()
        return page

    def _page_watermark(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.wm_enabled = QCheckBox("启用水印")
        self.wm_enabled.toggled.connect(self._watermark_toggled)
        layout.addWidget(self.wm_enabled)
        note = QLabel("勾选或改参数后，中间预览会马上按当前时间重画。平铺建议透明度 0.15 左右。图片 LOGO 不选文件时使用内置标志，也可以自己换。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        layout.addWidget(note)
        self.wm_style = QComboBox()
        for label, value in STYLE_LABELS:
            self.wm_style.addItem(label, value)
        self.wm_style.currentIndexChanged.connect(self._style_changed)
        layout.addWidget(self.wm_style)
        layout.addWidget(QLabel("文字"))
        self.wm_text = QLineEdit()
        self.wm_text.setPlaceholderText("水印文字")
        self.wm_text.textChanged.connect(self._on_form_changed)
        layout.addWidget(self.wm_text)
        image_row = QHBoxLayout()
        self.wm_image = QLineEdit()
        self.wm_image.setPlaceholderText("图片 LOGO")
        image_row.addWidget(self.wm_image, 1)
        image_row.addWidget(self._button("浏览…", self.choose_logo))
        layout.addLayout(image_row)
        self.wm_image.textChanged.connect(self._on_form_changed)
        pos = QGridLayout()
        self.wm_x = self._percent_spin(4)
        self.wm_y = self._percent_spin(6)
        self.wm_size = self._int_spin(8, 256, 36)
        self.wm_opacity = self._double_spin(0, 1, 0.05, 2, 0.30)
        self.wm_angle = self._double_spin(-180, 180, 1, 1, 0)
        self.wm_speed = self._double_spin(0, 2000, 5, 1, 0)
        self.wm_spacing = self._int_spin(40, 2000, 240)
        self.wm_start = self._double_spin(0, 86400, 0.1, 2, 0)
        self.wm_end = self._double_spin(0, 86400, 0.1, 2, 0)
        for widget in (self.wm_x, self.wm_y, self.wm_size, self.wm_opacity, self.wm_angle,
                       self.wm_speed, self.wm_spacing, self.wm_start, self.wm_end):
            widget.valueChanged.connect(self._on_form_changed)
        fields = [
            ("位置 X%", self.wm_x), ("位置 Y%", self.wm_y), ("大小", self.wm_size),
            ("透明度", self.wm_opacity), ("角度", self.wm_angle), ("速度", self.wm_speed),
            ("间距", self.wm_spacing), ("出现", self.wm_start), ("消失", self.wm_end),
        ]
        for index, (label, widget) in enumerate(fields):
            pos.addWidget(QLabel(label), index // 2, (index % 2) * 2)
            pos.addWidget(widget, index // 2, (index % 2) * 2 + 1)
        layout.addLayout(pos)
        layout.addWidget(QLabel("轨迹（0～1，分号分隔，至少两点）"))
        self.wm_path = QLineEdit()
        self.wm_path.setPlaceholderText("0.10,0.20; 0.80,0.30; 0.40,0.70")
        self.wm_path.textChanged.connect(self._on_form_changed)
        layout.addWidget(self.wm_path)
        font_row = QHBoxLayout()
        self.font_edit = QLineEdit()
        self.font_edit.setPlaceholderText("留空则自动找中文字体")
        font_row.addWidget(self.font_edit, 1)
        font_row.addWidget(self._button("字体…", self.choose_font))
        layout.addLayout(font_row)
        self.font_edit.textChanged.connect(self._on_form_changed)
        end_hint = QLabel("消失时间为 0 表示一直显示。速度单位是像素/秒。")
        end_hint.setObjectName("hint")
        end_hint.setWordWrap(True)
        layout.addWidget(end_hint)
        layout.addStretch()
        return page

    def _page_mask(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        warning = QLabel("暂停后在画面上拖动框选字幕，并设置时间段。框选后预览立刻显示模糊、复制或翻转。单条视频点「设为单独参数」。未点「添加蒙版」不会写入导出。复杂背景上这不是无痕修复。")
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.mask_list = QListWidget()
        self.mask_list.setMaximumHeight(110)
        self.mask_list.currentRowChanged.connect(self._load_mask_row)
        layout.addWidget(self.mask_list)
        self.mask_mode = QComboBox()
        for label, value in MASK_LABELS:
            self.mask_mode.addItem(label, value)
        self.mask_mode.currentIndexChanged.connect(self._on_mask_edited)
        layout.addWidget(self.mask_mode)
        grid = QGridLayout()
        self.mask_x = self._percent_spin(0)
        self.mask_y = self._percent_spin(78)
        self.mask_w = self._percent_spin(100)
        self.mask_h = self._percent_spin(16)
        self.mask_sx = self._percent_spin(0)
        self.mask_sy = self._percent_spin(60)
        self.mask_sw = self._percent_spin(100)
        self.mask_sh = self._percent_spin(16)
        for index, (label, widget) in enumerate((
            ("覆盖 X%", self.mask_x), ("覆盖 Y%", self.mask_y), ("覆盖宽%", self.mask_w), ("覆盖高%", self.mask_h),
            ("来源 X%", self.mask_sx), ("来源 Y%", self.mask_sy), ("来源宽%", self.mask_sw), ("来源高%", self.mask_sh),
        )):
            grid.addWidget(QLabel(label), index // 2, (index % 2) * 2)
            grid.addWidget(widget, index // 2, (index % 2) * 2 + 1)
            widget.valueChanged.connect(self._on_mask_edited)
        layout.addLayout(grid)
        extra = QHBoxLayout()
        extra.addWidget(QLabel("羽化"))
        self.mask_feather = self._int_spin(0, 80, 10)
        extra.addWidget(self.mask_feather)
        extra.addWidget(QLabel("透明"))
        self.mask_opacity = self._double_spin(0, 1, 0.05, 2, 1)
        extra.addWidget(self.mask_opacity)
        extra.addWidget(QLabel("模糊"))
        self.mask_blur = self._double_spin(0.2, 40, 0.5, 1, 12)
        extra.addWidget(self.mask_blur)
        for widget in (self.mask_feather, self.mask_opacity, self.mask_blur):
            widget.valueChanged.connect(self._on_mask_edited)
        layout.addLayout(extra)
        times = QHBoxLayout()
        self.mask_start = self._double_spin(0, 86400, 0.1, 2, 0)
        self.mask_end = self._double_spin(0, 86400, 0.1, 2, 0)
        self.mask_start.valueChanged.connect(self._on_mask_edited)
        self.mask_end.valueChanged.connect(self._on_mask_edited)
        times.addWidget(QLabel("开始"))
        times.addWidget(self.mask_start)
        times.addWidget(self._button("用当前", lambda: self.mask_start.setValue(self.playhead)))
        times.addWidget(QLabel("结束"))
        times.addWidget(self.mask_end)
        times.addWidget(self._button("用当前", lambda: self.mask_end.setValue(self.playhead)))
        layout.addLayout(times)
        actions = QHBoxLayout()
        actions.addWidget(self._button("添加蒙版", self.add_mask))
        actions.addWidget(self._button("更新选中", self.update_mask))
        actions.addWidget(self._button("删除选中", self.delete_mask))
        layout.addLayout(actions)
        layout.addStretch()
        return page

    def _page_dedup(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        pip_box = QGroupBox("画中画")
        pip = QVBoxLayout(pip_box)
        self.pip_enabled = QCheckBox("启用画中画")
        self.pip_enabled.toggled.connect(self._on_form_changed)
        pip.addWidget(self.pip_enabled)
        note = QLabel("叠一段自己拍的视频。默认静音，不改变镜头顺序。未勾选或未选视频时不会写入命令。")
        note.setWordWrap(True)
        note.setObjectName("hint")
        pip.addWidget(note)
        path_row = QHBoxLayout()
        self.pip_path = QLineEdit()
        self.pip_path.setPlaceholderText("选择叠加视频")
        self.pip_path.textChanged.connect(self._on_form_changed)
        path_row.addWidget(self.pip_path, 1)
        path_row.addWidget(self._button("浏览…", self.choose_pip))
        pip.addLayout(path_row)
        self.pip_position = QComboBox()
        for label, value in (("左上", "tl"), ("右上", "tr"), ("左下", "bl"), ("右下", "br"), ("居中", "center")):
            self.pip_position.addItem(label, value)
        self.pip_position.setCurrentIndex(1)
        self.pip_position.currentIndexChanged.connect(self._on_form_changed)
        pip.addWidget(self.pip_position)
        grid = QGridLayout()
        self.pip_size = self._percent_spin(22)
        self.pip_opacity = self._percent_spin(8)
        self.pip_opacity_jitter = self._percent_spin(3)
        self.pip_position_jitter = self._percent_spin(2)
        self.pip_size_jitter = self._percent_spin(0)
        self.pip_audio = self._double_spin(0, 100, 1, 0, 0)
        self.pip_start = self._double_spin(0, 86400, 0.1, 2, 0)
        self.pip_end = self._double_spin(0, 86400, 0.1, 2, 0)
        for widget in (self.pip_size, self.pip_opacity, self.pip_opacity_jitter, self.pip_position_jitter,
                       self.pip_size_jitter, self.pip_audio, self.pip_start, self.pip_end):
            widget.valueChanged.connect(self._on_form_changed)
        fields = [
            ("宽度%", self.pip_size), ("透明度%", self.pip_opacity),
            ("透明度浮动", self.pip_opacity_jitter), ("位置浮动", self.pip_position_jitter),
            ("尺寸浮动", self.pip_size_jitter), ("音量%", self.pip_audio),
            ("出现", self.pip_start), ("结束", self.pip_end),
        ]
        for index, (label, widget) in enumerate(fields):
            grid.addWidget(QLabel(label), index // 2, (index % 2) * 2)
            grid.addWidget(widget, index // 2, (index % 2) * 2 + 1)
        pip.addLayout(grid)
        self.pip_short = QComboBox()
        self.pip_short.addItem("比主片短时循环", "loop")
        self.pip_short.addItem("比主片短时停在尾帧", "hold")
        self.pip_short.currentIndexChanged.connect(self._on_form_changed)
        pip.addWidget(self.pip_short)
        hint = QLabel("音量 0 表示静音，1 表示保留 1% 音量。结束为 0 表示直到主片结束。浮动让各版本略有差别，同一种子可复现。")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        pip.addWidget(hint)
        layout.addWidget(pip_box)

        self.photo_box = QGroupBox("真实照片加帧")
        photos = QVBoxLayout(self.photo_box)
        self.photo_enabled = QCheckBox("启用照片加帧")
        self.photo_enabled.toggled.connect(self._on_form_changed)
        photos.addWidget(self.photo_enabled)
        warn = QLabel("把照片插进时间轴，成片会变长，镜头之间会多出这些画面。成片二次处理模式不可用。未勾选或没有照片时不会写入命令。")
        warn.setWordWrap(True)
        warn.setObjectName("warning")
        photos.addWidget(warn)
        self.photo_list = QListWidget()
        self.photo_list.setMaximumHeight(90)
        photos.addWidget(self.photo_list)
        buttons = QHBoxLayout()
        buttons.addWidget(self._button("添加照片", self.add_photos))
        buttons.addWidget(self._button("移除选中", self.remove_photo))
        photos.addLayout(buttons)
        photo_grid = QGridLayout()
        self.photo_interval = self._double_spin(0.2, 120, 0.1, 2, 3)
        self.photo_jitter = self._double_spin(0, 30, 0.1, 2, 0.4)
        self.photo_hold = self._int_spin(40, 5000, 400)
        self.photo_hold_jitter = self._int_spin(0, 2000, 80)
        self.photo_fade = self._int_spin(0, 2000, 120)
        for widget in (self.photo_interval, self.photo_jitter, self.photo_hold, self.photo_hold_jitter, self.photo_fade):
            widget.valueChanged.connect(self._on_form_changed)
        for index, (label, widget) in enumerate((
            ("间隔秒", self.photo_interval), ("间隔浮动", self.photo_jitter),
            ("停留毫秒", self.photo_hold), ("停留浮动", self.photo_hold_jitter),
            ("淡入淡出", self.photo_fade),
        )):
            photo_grid.addWidget(QLabel(label), index, 0)
            photo_grid.addWidget(widget, index, 1)
        photos.addLayout(photo_grid)
        fit = QLabel("照片按画面居中适配，上下或左右留黑边，不拉伸。多张照片轮流插入。")
        fit.setWordWrap(True)
        fit.setObjectName("hint")
        photos.addWidget(fit)
        layout.addWidget(self.photo_box)
        layout.addStretch()
        return page

    def _page_export(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(QLabel("编码"))
        self.codec = QComboBox()
        self.codec.addItem("H.264", "libx264")
        self.codec.addItem("H.265", "libx265")
        self.codec.currentIndexChanged.connect(self._on_form_changed)
        layout.addWidget(self.codec)
        quality = QHBoxLayout()
        quality.addWidget(QLabel("CRF"))
        self.crf = self._int_spin(14, 30, 19)
        quality.addWidget(self.crf)
        quality.addWidget(QLabel("速度"))
        self.encoder_preset = QComboBox()
        for name in ENCODER_PRESETS:
            self.encoder_preset.addItem(name, name)
        self.encoder_preset.setCurrentText("medium")
        self.encoder_preset.currentIndexChanged.connect(self._on_form_changed)
        quality.addWidget(self.encoder_preset, 1)
        layout.addLayout(quality)
        self.codec_hint = QLabel("未检测到的编码器不会出现在列表里。NVENC 需要本机显卡，失败会记入该任务。")
        self.codec_hint.setWordWrap(True)
        self.codec_hint.setObjectName("hint")
        layout.addWidget(self.codec_hint)
        files = QHBoxLayout()
        files.addWidget(self._button("保存预设", self.save_preset))
        files.addWidget(self._button("删除此预设", self.delete_preset))
        files.addWidget(self._button("保存项目", self.save_project))
        files.addWidget(self._button("打开项目", self.load_project))
        layout.addLayout(files)
        files2 = QHBoxLayout()
        files2.addWidget(self._button("查看任务参数", self.show_parameters))
        files2.addWidget(self._button("任务记录", self.show_history))
        layout.addLayout(files2)
        self.advanced_button = QPushButton("高级：FFmpeg / FFprobe 路径")
        self.advanced_button.setCheckable(True)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_button)
        self.advanced = QWidget()
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.ffmpeg_edit = QLineEdit("ffmpeg")
        self.ffprobe_edit = QLineEdit("ffprobe")
        self.ffmpeg_edit.setPlaceholderText("ffmpeg，或 C:\\ffmpeg\\bin\\ffmpeg.exe")
        self.ffprobe_edit.setPlaceholderText("ffprobe，或 C:\\ffmpeg\\bin\\ffprobe.exe")
        advanced_layout.addWidget(QLabel("FFmpeg"))
        ffmpeg_row = QHBoxLayout()
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(self._button("浏览…", lambda: self._browse_exe(self.ffmpeg_edit)))
        advanced_layout.addLayout(ffmpeg_row)
        advanced_layout.addWidget(QLabel("FFprobe"))
        probe_row = QHBoxLayout()
        probe_row.addWidget(self.ffprobe_edit, 1)
        probe_row.addWidget(self._button("浏览…", lambda: self._browse_exe(self.ffprobe_edit)))
        advanced_layout.addLayout(probe_row)
        advanced_layout.addWidget(self._button("检查 FFmpeg 环境", self.check_environment))
        self.advanced.setVisible(False)
        layout.addWidget(self.advanced)
        self.export_warning = QLabel()
        self.export_warning.setWordWrap(True)
        self.export_warning.setObjectName("warning")
        layout.addWidget(self.export_warning)
        layout.addStretch()
        return page

    def _queue_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        toolbar = QHBoxLayout()
        self.pause_btn = self._button("暂停", self.toggle_pause)
        toolbar.addWidget(self.pause_btn)
        toolbar.addWidget(self._button("取消选中", self.cancel_selected))
        toolbar.addWidget(self._button("取消全部", self.cancel_all))
        toolbar.addWidget(self._button("重试失败", self.retry_selected))
        toolbar.addStretch()
        self.count_label = QLabel("0 项")
        self.count_label.setObjectName("hint")
        toolbar.addWidget(self.count_label)
        layout.addLayout(toolbar)
        self.task_table = QTableWidget(0, 6)
        self.task_table.setHorizontalHeaderLabels(["源视频", "版本", "状态", "进度", "输出结果", "任务ID"])
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.task_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (1, 2, 3, 5):
            self.task_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.task_table.setColumnHidden(5, True)
        self.task_table.setMinimumHeight(60)
        self.task_table.itemDoubleClicked.connect(lambda *_: self.show_parameters())
        layout.addWidget(self.task_table)
        return frame

    def _log_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("运行日志（同时写入 ~/.yingxu/logs）"))
        row.addStretch()
        row.addWidget(self._button("清空日志", lambda: self.log_edit.setPlainText("")))
        layout.addLayout(row)
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(1200)
        layout.addWidget(self.log_edit, 1)
        return frame

    def _button(self, text: str, handler, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(handler)
        return button

    def _int_spin(self, low: int, high: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        return spin

    def _double_spin(self, low: float, high: float, step: float, decimals: int, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(value)
        return spin

    def _percent_spin(self, value: float) -> QDoubleSpinBox:
        return self._double_spin(0, 100, 0.5, 1, value)

    def _range(self, layout: QVBoxLayout, title: str, low: float, high: float, step: float,
               decimals: int, start: float, end: float) -> RangeRow:
        row = RangeRow(title, low, high, step, decimals, start, end)
        row.changed.connect(self._on_form_changed)
        layout.addWidget(row)
        return row

    def _page_changed(self, index: int) -> None:
        if not hasattr(self, "stack"):
            return
        self.stack.setCurrentIndex(index)
        self._refresh_summary()
        if index == 3 and self.view_mode != "still":
            self._show_still("字幕蒙版：在暂停画面上按住鼠标拖动框选字幕区域")

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced.setVisible(checked)

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        film = mode == "film"
        for widget in (self.mirror_h, self.mirror_v, self.crop_enabled, self.drop_enabled):
            widget.setEnabled(not film)
            if film:
                widget.setChecked(False)
        self.rotate.setEnabled(not film)
        if film:
            self.rotate.uncheck()
        self.photo_box.setEnabled(not film)
        if film:
            self.photo_enabled.setChecked(False)
        hints = {
            "single": "选中一条素材，按版本数导出多个成片。",
            "batch": "全部素材进队列。某一条失败不影响其他条目。",
            "film": "不重排镜头。镜像、旋转、裁剪、抽帧和照片加帧已锁定。画中画仍可用。",
        }
        self.mode_hint.setText(hints[mode])
        self._refresh_summary()

    def _set_preview_mode(self, mode: str) -> None:
        self.preview_mode = mode
        (self.original_button if mode == "original" else self.processed_button).setChecked(True)
        self._show_still()

    def _on_form_changed(self, *_args) -> None:
        if self._loading:
            return
        self._mark_custom()
        self._refresh_summary()
        if self._effects_on():
            self.preview_mode = "processed"
            self.processed_button.setChecked(True)
            if self.view_mode == "compare":
                self._show_still()
                return
            if self.view_mode == "play":
                self.preview_stack.setCurrentWidget(self.canvas)
                self._request_preview()
                return
        self._schedule_preview()

    def _effects_on(self) -> bool:
        try:
            return visual_effects(self._preview_options())
        except Exception:
            return False

    def _preview_options(self) -> ProcessOptions:
        options = apply_default_logo(self.collect_options(validate=False))
        draft = None
        try:
            draft = self._mask_from_form()
        except Exception:
            draft = None
        return prepare_preview_options(options, draft, self._mask_preview)

    def _mark_custom(self) -> None:
        if self.preset_choice.currentData() == "custom":
            return
        self._loading = True
        index = self.preset_choice.findData("custom")
        if index >= 0:
            self.preset_choice.setCurrentIndex(index)
        self._loading = False

    def _style_changed(self, _index: int = 0) -> None:
        if not self._loading:
            style = self.wm_style.currentData()
            if style in {"move_h", "move_v", "path"} and self.wm_speed.value() <= 0:
                self.wm_speed.setValue(60)
            if style == "diagonal" and abs(self.wm_angle.value()) < 0.1:
                self.wm_angle.setValue(-24)
            if style == "translucent" and self.wm_opacity.value() > 0.45:
                self.wm_opacity.setValue(0.25)
            self._ensure_watermark_defaults()
        self._on_form_changed()

    def _watermark_toggled(self, checked: bool) -> None:
        if checked and not self._loading:
            self._ensure_watermark_defaults()
        self._on_form_changed()

    def _ensure_watermark_defaults(self) -> None:
        if not self.wm_enabled.isChecked():
            return
        style = self.wm_style.currentData()
        self._loading = True
        if style == "image" and not self.wm_image.text().strip():
            self.wm_image.setText(str(default_logo_path()))
        elif style not in {"image", "path"} and not self.wm_text.text().strip():
            self.wm_text.setText("影序")
        self._loading = False

    def _on_mask_edited(self, *_args) -> None:
        if self._loading:
            return
        self._mask_preview = True
        self._on_form_changed()

    def _roll_seed(self) -> None:
        import random
        self.seed.setValue(random.randint(1, 999999))

    def _refresh_summary(self) -> None:
        try:
            options = self.collect_options(validate=False)
            text = "、".join(enabled_summary(options))
            warnings = risky_warnings(options)
        except ValueError as exc:
            text = str(exc)
            warnings = []
        self.summary_label.setText("本批次：" + text)
        self.export_warning.setText("\n".join(warnings))

    def collect_options(self, validate: bool = True) -> ProcessOptions:
        points = self._parse_points(self.wm_path.text())
        options = ProcessOptions(
            scale=self.scale.spec(), crop_enabled=self.crop_enabled.isChecked(),
            crop_x=self.crop_x.value(), crop_y=self.crop_y.value(),
            crop_w=self.crop_w.value(), crop_h=self.crop_h.value(),
            mirror_h=self.mirror_h.isChecked(), mirror_v=self.mirror_v.isChecked(),
            rotate=self.rotate.spec(), brightness=self.brightness.spec(),
            contrast=self.contrast.spec(), saturation=self.saturation.spec(),
            color_temp=self.color_temp.spec(), sharpen=self.sharpen.spec(), blur=self.blur.spec(),
            speed=self.speed.spec(), drop_enabled=self.drop_enabled.isChecked(),
            drop_interval=self.drop_interval.value(), fps=int(self.fps.currentData() or 0),
            codec=str(self.codec.currentData() or "libx264"), crf=self.crf.value(),
            encoder_preset=str(self.encoder_preset.currentData() or "medium"),
            volume_db=self.volume.spec(), denoise=self.denoise.isChecked(),
            denoise_nr=self.denoise_nr.value(), eq_enabled=self.eq_enabled.isChecked(),
            eq_low=self.eq_low.value(), eq_mid=self.eq_mid.value(), eq_high=self.eq_high.value(),
            loudnorm=self.loudnorm.isChecked(), mute=self.mute.isChecked(),
            bgm_mode=str(self.bgm_mode.currentData() or "off"), bgm_path=self.bgm_path.text().strip(),
            bgm_volume=self.bgm_volume.value(),
            watermark=WatermarkSpec(
                enabled=self.wm_enabled.isChecked(), style=str(self.wm_style.currentData() or "text"),
                text=self.wm_text.text(),
                image=self.wm_image.text().strip() or (
                    str(default_logo_path()) if self.wm_style.currentData() == "image" else ""),
                x=self.wm_x.value() / 100, y=self.wm_y.value() / 100, size=self.wm_size.value(),
                opacity=self.wm_opacity.value(), angle=self.wm_angle.value(), speed=self.wm_speed.value(),
                start=self.wm_start.value(), end=self.wm_end.value(), spacing=self.wm_spacing.value(),
                path_points=points, font=self.font_edit.text().strip(),
            ),
            masks=tuple(self.masks), font=self.font_edit.text().strip(),
            pip=PipSpec(
                enabled=self.pip_enabled.isChecked(), path=self.pip_path.text().strip(),
                position=str(self.pip_position.currentData() or "tr"),
                size=self.pip_size.value() / 100, opacity=self.pip_opacity.value() / 100,
                opacity_jitter=self.pip_opacity_jitter.value() / 100,
                position_jitter=self.pip_position_jitter.value() / 100,
                size_jitter=self.pip_size_jitter.value() / 100,
                audio_percent=self.pip_audio.value(),
                short_mode=str(self.pip_short.currentData() or "loop"),
                start=self.pip_start.value(), end=self.pip_end.value(),
            ),
            photos=PhotoInsertSpec(
                enabled=self.photo_enabled.isChecked(), paths=tuple(self.photo_paths),
                interval=self.photo_interval.value(), interval_jitter=self.photo_jitter.value(),
                hold_ms=self.photo_hold.value(), hold_jitter_ms=self.photo_hold_jitter.value(),
                fade_ms=self.photo_fade.value(),
            ),
        )
        if self.mode == "film":
            options = self._strip_film(options)
        if validate:
            validate_options(options)
        return options

    def apply_options(self, options: ProcessOptions) -> None:
        self._loading = True
        try:
            self.scale.set_spec(options.scale)
            self.crop_enabled.setChecked(options.crop_enabled)
            self.crop_x.setValue(options.crop_x)
            self.crop_y.setValue(options.crop_y)
            self.crop_w.setValue(options.crop_w)
            self.crop_h.setValue(options.crop_h)
            self.mirror_h.setChecked(options.mirror_h)
            self.mirror_v.setChecked(options.mirror_v)
            self.rotate.set_spec(options.rotate)
            self.brightness.set_spec(options.brightness)
            self.contrast.set_spec(options.contrast)
            self.saturation.set_spec(options.saturation)
            self.color_temp.set_spec(options.color_temp)
            self.sharpen.set_spec(options.sharpen)
            self.blur.set_spec(options.blur)
            self.speed.set_spec(options.speed)
            self.drop_enabled.setChecked(options.drop_enabled)
            self.drop_interval.setValue(options.drop_interval)
            self._set_combo(self.fps, options.fps)
            self._set_combo(self.codec, options.codec)
            self.crf.setValue(options.crf)
            self._set_combo(self.encoder_preset, options.encoder_preset)
            self.volume.set_spec(options.volume_db)
            self.denoise.setChecked(options.denoise)
            self.denoise_nr.setValue(options.denoise_nr)
            self.eq_enabled.setChecked(options.eq_enabled)
            self.eq_low.setValue(options.eq_low)
            self.eq_mid.setValue(options.eq_mid)
            self.eq_high.setValue(options.eq_high)
            self.loudnorm.setChecked(options.loudnorm)
            self.mute.setChecked(options.mute)
            self._set_combo(self.bgm_mode, options.bgm_mode)
            self.bgm_path.setText(options.bgm_path)
            self.bgm_volume.setValue(options.bgm_volume)
            mark = options.watermark
            self.wm_enabled.setChecked(mark.enabled)
            self._set_combo(self.wm_style, mark.style)
            self.wm_text.setText(mark.text)
            self.wm_image.setText(mark.image)
            self.wm_x.setValue(mark.x * 100)
            self.wm_y.setValue(mark.y * 100)
            self.wm_size.setValue(mark.size)
            self.wm_opacity.setValue(mark.opacity)
            self.wm_angle.setValue(mark.angle)
            self.wm_speed.setValue(mark.speed)
            self.wm_spacing.setValue(mark.spacing)
            self.wm_start.setValue(mark.start)
            self.wm_end.setValue(mark.end)
            self.wm_path.setText("; ".join(f"{x:.2f},{y:.2f}" for x, y in mark.path_points))
            self.font_edit.setText(options.font or mark.font)
            self.masks = list(options.masks)
            self._refresh_mask_list()
            pip = options.pip
            self.pip_enabled.setChecked(pip.enabled)
            self.pip_path.setText(pip.path)
            self._set_combo(self.pip_position, pip.position)
            self.pip_size.setValue(pip.size * 100)
            self.pip_opacity.setValue(pip.opacity * 100)
            self.pip_opacity_jitter.setValue(pip.opacity_jitter * 100)
            self.pip_position_jitter.setValue(pip.position_jitter * 100)
            self.pip_size_jitter.setValue(pip.size_jitter * 100)
            self.pip_audio.setValue(pip.audio_percent)
            self._set_combo(self.pip_short, pip.short_mode)
            self.pip_start.setValue(pip.start)
            self.pip_end.setValue(pip.end)
            self.photo_enabled.setChecked(options.photos.enabled)
            self.photo_paths = list(options.photos.paths)
            self._refresh_photo_list()
            self.photo_interval.setValue(options.photos.interval)
            self.photo_jitter.setValue(options.photos.interval_jitter)
            self.photo_hold.setValue(options.photos.hold_ms)
            self.photo_hold_jitter.setValue(options.photos.hold_jitter_ms)
            self.photo_fade.setValue(options.photos.fade_ms)
        finally:
            self._loading = False
        self._refresh_summary()

    def _strip_film(self, options: ProcessOptions) -> ProcessOptions:
        data = options.to_dict()
        data.update({"mirror_h": False, "mirror_v": False, "crop_enabled": False, "drop_enabled": False,
                     "rotate": {"enabled": False, "low": 0, "high": 0}})
        photos = dict(data.get("photos") or {})
        photos["enabled"] = False
        data["photos"] = photos
        return ProcessOptions.from_dict(data)

    def _parse_points(self, text: str) -> tuple[tuple[float, float], ...]:
        points = []
        try:
            for part in text.split(";"):
                part = part.strip()
                if not part:
                    continue
                left, right = part.split(",", 1)
                points.append((float(left), float(right)))
        except ValueError as exc:
            raise ValueError("轨迹格式应为 0.10,0.20; 0.80,0.30") from exc
        return tuple(points)

    def _set_combo(self, combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load_presets(self) -> None:
        self._loading = True
        self.preset_choice.clear()
        selected = 0
        try:
            for path in sorted(preset_dir().glob("*.json")):
                preset = Preset.from_file(path)
                self.preset_choice.addItem(preset.name, ("builtin", str(path)))
                if preset.name.startswith("标准处理"):
                    selected = self.preset_choice.count() - 1
        except (OSError, ValueError) as exc:
            self.write_log(f"内置预设读取失败：{exc}")
        for path in sorted(user_preset_dir().glob("*.json")):
            try:
                name = json.loads(path.read_text(encoding="utf-8")).get("name") or path.stem
            except (OSError, ValueError):
                continue
            self.preset_choice.addItem(name, ("profile", str(path)))
        self.preset_choice.addItem("自定义", "custom")
        self.preset_choice.setCurrentIndex(selected)
        self._loading = False
        self._preset_changed()

    def _preset_changed(self, _index: int = 0) -> None:
        if self._loading:
            return
        data = self.preset_choice.currentData()
        if data == "custom" or data is None:
            self._refresh_summary()
            return
        try:
            if data[0] == "builtin":
                options = options_from_preset(Preset.from_file(data[1]))
            else:
                options = load_profile(data[1])
        except (OSError, ValueError) as exc:
            self.show_error(f"预设读取失败：{exc}")
            return
        self.apply_options(options)
        self.write_log(f"已载入预设：{self.preset_choice.currentText()}")

    def write_log(self, message: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {message}"
        self.log_edit.appendPlainText(line)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "影序", message)
        self.write_log(message)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        self._add_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])

    def import_sources(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", str(Path.home()),
            "视频 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts);;所有文件 (*.*)")
        self._add_paths(paths)

    def _add_paths(self, raw_paths: list[str]) -> None:
        added = 0
        for raw in raw_paths:
            path = Path(raw)
            candidates = [path]
            if path.is_dir():
                candidates = [item for item in sorted(path.iterdir()) if item.is_file()]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
                if candidate.suffix.lower() not in VIDEO_EXTS:
                    self.write_log(f"跳过非视频文件：{candidate.name}")
                    continue
                if resolved in self.sources:
                    continue
                self.sources.append(resolved)
                self._queue_probe(str(resolved))
                added += 1
        if added:
            self._refresh_source_list()
            self.write_log(f"导入 {added} 个视频，当前 {len(self.sources)} 个")
            self._schedule_preview()

    def _refresh_source_list(self) -> None:
        current = self.source_list.currentItem()
        current_path = current.data(Qt.UserRole) if current else None
        self.source_list.clear()
        names = [path.name for path in self.sources]
        for path in self.sources:
            label = path.name if names.count(path.name) == 1 else f"{path.name}  —  {path.parent.name}"
            if str(path) in self.overrides:
                label += "  · 单独参数"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.source_list.addItem(item)
            if str(path) == current_path:
                self.source_list.setCurrentItem(item)
        if self.source_list.currentRow() < 0 and self.source_list.count():
            self.source_list.setCurrentRow(0)

    def remove_sources(self) -> None:
        for item in list(self.source_list.selectedItems()):
            path = Path(item.data(Qt.UserRole))
            if path in self.sources:
                self.sources.remove(path)
            self.overrides.pop(str(path), None)
            self.media.pop(str(path), None)
        self._refresh_source_list()

    def clear_sources(self) -> None:
        self.sources.clear()
        self.overrides.clear()
        self.media.clear()
        self._refresh_source_list()

    def choose_output(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择导出目录", self.output_edit.text() or str(Path.home()))
        if chosen:
            self.output_edit.setText(chosen)

    def choose_bgm(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "选择背景音乐", str(Path.home()), "媒体 (*.mp3 *.wav *.aac *.m4a *.flac *.mp4 *.mov *.mkv);;所有文件 (*.*)")
        if chosen:
            self.bgm_path.setText(chosen)

    def choose_logo(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "选择水印图片", str(Path.home()), "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*.*)")
        if chosen:
            self.wm_image.setText(chosen)
            if not self.wm_enabled.isChecked():
                self.wm_enabled.setChecked(True)

    def choose_pip(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择画中画视频", str(Path.home()),
            "视频 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts);;所有文件 (*.*)")
        if chosen:
            self.pip_path.setText(chosen)
            if not self.pip_enabled.isChecked():
                self.pip_enabled.setChecked(True)

    def add_photos(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(
            self, "选择照片", str(Path.home()),
            "照片 (*.jpg *.jpeg *.png *.webp *.bmp);;所有文件 (*.*)")
        added = 0
        for raw in chosen:
            path = str(Path(raw).resolve())
            if Path(path).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
                self.write_log(f"跳过不支持的照片：{Path(path).name}")
                continue
            if path not in self.photo_paths:
                self.photo_paths.append(path)
                added += 1
        if added:
            if not self.photo_enabled.isChecked() and self.photo_box.isEnabled():
                self.photo_enabled.setChecked(True)
            self._refresh_photo_list()
            self._on_form_changed()
            self.write_log(f"已添加 {added} 张照片，当前 {len(self.photo_paths)} 张")

    def remove_photo(self) -> None:
        row = self.photo_list.currentRow()
        if row < 0 or row >= len(self.photo_paths):
            return
        del self.photo_paths[row]
        self._refresh_photo_list()
        self._on_form_changed()

    def _refresh_photo_list(self) -> None:
        self.photo_list.clear()
        for path in self.photo_paths:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            self.photo_list.addItem(item)

    def choose_font(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "选择字体", str(Path.home()), "字体 (*.ttf *.otf *.ttc);;所有文件 (*.*)")
        if chosen:
            self.font_edit.setText(chosen)

    def _browse_exe(self, edit: QLineEdit) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "选择程序", edit.text() or str(Path.home()))
        if chosen:
            edit.setText(chosen)

    def _queue_probe(self, path: str) -> None:
        self.probe_queue.append(path)
        self._pump_probe()

    def _pump_probe(self) -> None:
        if self.probe_thread and self.probe_thread.isRunning():
            return
        if not self.probe_queue:
            return
        path = self.probe_queue.pop(0)
        self.probe_thread = ProbeThread(path, self.ffprobe_edit.text().strip())
        self.probe_thread.probed.connect(self._on_probed)
        self.probe_thread.finished.connect(self._pump_probe)
        self.probe_thread.start()

    def _on_probed(self, path: str, info: object, error: str) -> None:
        if error or not isinstance(info, dict):
            self.write_log(f"无法读取 {Path(path).name}：{error}")
            return
        self.media[path] = info
        current = self._preview_source()
        if current and str(current) == path:
            self._apply_source_info(info)
            self._schedule_preview()

    def _preview_source(self) -> Path | None:
        item = self.source_list.currentItem()
        if item:
            return Path(item.data(Qt.UserRole))
        return self.sources[0] if self.sources else None

    def _schedule_preview(self) -> None:
        self.preview_timer.start(90)

    def _request_preview(self) -> None:
        self._preview_wanted = True
        if self.preview_thread and self.preview_thread.isRunning():
            return
        self._preview_wanted = False
        source = self._preview_source()
        info = self.media.get(str(source)) if source else None
        if source is None or not info:
            return
        options = None
        mode = self.preview_mode
        if mode != "original":
            try:
                options = self._preview_options()
                mode = "processed" if visual_effects(options) else "original"
            except Exception as exc:
                self.preview_info.setText(str(exc))
                return
        self._token += 1
        output = self.preview_dir / f"frame-{self._token}.png"
        self.preview_thread = PreviewThread(
            self._token, mode, source, info, options or ProcessOptions(),
            self.playhead, output, self._ffmpeg_path(), self._preview_seed(source),
        )
        self.preview_thread.ok.connect(self._on_preview_ok)
        self.preview_thread.fail.connect(self._on_preview_fail)
        self.preview_thread.finished.connect(self._preview_finished)
        self.preview_thread.start()

    def _preview_finished(self) -> None:
        if self._preview_wanted:
            self._request_preview()

    def _on_preview_ok(self, token: int, path: str) -> None:
        if token != self._token:
            return
        source = self._preview_source()
        info = self.media.get(str(source), {}) if source else {}
        self.canvas.set_frame(path, int(info.get("width") or 0), int(info.get("height") or 0))
        if self.view_mode == "still":
            self.preview_stack.setCurrentWidget(self.canvas)
        duration = float(info.get("duration") or 0)
        label = "处理后" if self.preview_mode == "processed" else "原片"
        self.preview_info.setText(
            f"{label} · {fmt_time(self.playhead)} / {fmt_time(duration)} · 预览使用版本 1 的随机参数")

    def _on_preview_fail(self, token: int, message: str) -> None:
        if token == self._token:
            self.preview_info.setText(message)
            self.write_log(message)

    # ----- playback -----
    def _duration(self) -> float:
        source = self._preview_source()
        info = self.media.get(str(source), {}) if source else {}
        return float(info.get("duration") or 0)

    def _apply_source_info(self, info: dict) -> None:
        duration = float(info["duration"])
        self.slider.setRange(0, max(0, int(duration * 1000)))
        self.playhead = min(self.playhead, duration)
        self.slider.setValue(int(self.playhead * 1000))
        self._update_time_label()
        self.preview_info.setText(f"{info['width']}×{info['height']} · {fmt_time(duration)} · {info['fps']:.2f} fps")

    def _on_source_changed(self, _row: int) -> None:
        source = self._preview_source()
        if str(source) == getattr(self, "_last_source", None):
            return
        self._last_source = str(source)
        self._stop_play()
        self.single_pane.release()
        self.cmp_left.release()
        self.cmp_right.release()
        self.compare = None
        self.view_mode = "still"
        self.preview_stack.setCurrentWidget(self.canvas)
        self.playhead = 0.0
        source = self._preview_source()
        info = self.media.get(str(source)) if source else None
        if info:
            self._apply_source_info(info)
        self._schedule_preview()

    def _update_time_label(self) -> None:
        self.time_label.setText(f"{fmt_time(self.playhead)} / {fmt_time(self._duration())}")

    def _set_volume(self, value: int) -> None:
        for pane in (self.single_pane, self.cmp_left, self.cmp_right):
            pane.set_volume(value / 100)

    def _show_still(self, message: str | None = None) -> None:
        """Pause players and show an exact FFmpeg frame; mask/crop drag-select works on it."""
        self._stop_play()
        self.view_mode = "still"
        self.compare = None
        self.preview_stack.setCurrentWidget(self.canvas)
        if message:
            self.preview_info.setText(message)
        self._schedule_preview()

    def _seek(self, value: int) -> None:
        self.playhead = value / 1000
        self._update_time_label()
        if self.view_mode == "compare" and self.compare:
            start = self.compare["start_ms"]
            rel = max(0, min(self.compare["length_ms"], value - start))
            self.cmp_right.set_position(rel)
            self.cmp_left.set_position(start + rel)
            return
        if self.view_mode == "play":
            self.single_pane.set_position(max(0, value - self._single_base))
            if self.preview_mode == "processed":
                self.preview_stack.setCurrentWidget(self.canvas)
                self._request_preview()
            return
        self._schedule_preview()

    def _step_frame(self, direction: int) -> None:
        if self.view_mode != "still":
            self._show_still()
        source = self._preview_source()
        info = self.media.get(str(source), {}) if source else {}
        fps = float(info.get("fps") or 25)
        self.playhead = min(self._duration() or 1e9, max(0.0, self.playhead + direction / fps))
        self.slider.setValue(int(self.playhead * 1000))
        self._update_time_label()
        self._schedule_preview()

    def toggle_play(self) -> None:
        if self._playing:
            self._pause_playback()
            return
        source = self._preview_source()
        if not source:
            self.show_error("请先导入视频。")
            return
        if not VideoPane.available():
            self.show_error("当前 Python 环境缺少 PySide6 QtMultimedia，无法播放。")
            return
        if self.view_mode == "compare" and self.compare:
            if self.cmp_right.at_end():
                self.cmp_right.set_position(0)
                self.cmp_left.set_position(self.compare["start_ms"])
            self.cmp_left.play()
            self.cmp_right.play()
            self._set_playing(True)
            return
        duration = self._duration()
        if self.preview_mode == "processed" and self._effects_on():
            self._play_live_effects(source)
            return
        if duration and self.playhead >= duration - 0.05:
            self.playhead = 0.0
        self._play_single(str(source), 0.0, self.playhead, "原片播放")

    def _play_live_effects(self, source: Path) -> None:
        """Play source audio while the canvas keeps redrawing the processed frame."""
        self.cmp_left.pause()
        self.cmp_right.pause()
        self.compare = None
        self._single_base = 0
        self.preview_stack.setCurrentWidget(self.canvas)
        self.view_mode = "play"
        self.single_pane.load(str(source), int(self.playhead * 1000))
        self.single_pane.play()
        self._set_playing(True)
        self._last_live = 0.0
        self._request_preview()

    def _play_single(self, path: str, base: float, at: float, title: str) -> None:
        self.cmp_left.pause()
        self.cmp_right.pause()
        self.compare = None
        self._single_base = int(base * 1000)
        self.single_pane.title.setText(title)
        self.single_pane.load(path, int((at - base) * 1000))
        self.view_mode = "play"
        self.preview_stack.setCurrentWidget(self.single_pane)
        self.single_pane.play()
        self._set_playing(True)

    def _set_playing(self, playing: bool) -> None:
        self._playing = playing
        self.play_button.setText("暂停" if playing else "播放")
        if playing:
            self.ui_timer.start()
        else:
            self.ui_timer.stop()

    def _pause_playback(self) -> None:
        if self.view_mode == "compare":
            self.cmp_left.pause()
            self.cmp_right.pause()
            self._set_playing(False)
            self._tick_playback(force=True)
            return
        if self.view_mode == "play":
            self.single_pane.pause()
            self.playhead = (self._single_base + self.single_pane.position()) / 1000
            self._set_playing(False)
            self.slider.setValue(int(self.playhead * 1000))
            self._show_still()
            return
        self._set_playing(False)

    def _tick_playback(self, force: bool = False) -> None:
        if self.view_mode == "compare" and self.compare:
            start, length = self.compare["start_ms"], self.compare["length_ms"]
            rel = self.cmp_right.position()
            if self._playing:
                drift = self.cmp_left.position() - (start + rel)
                if abs(drift) > 120:
                    self.cmp_left.set_position(start + rel)
            position = start + rel
            if self._playing and (self.cmp_right.at_end() or rel >= length - 40):
                self.cmp_left.pause()
                self.cmp_right.pause()
                self._set_playing(False)
        elif self.view_mode == "play":
            position = self._single_base + self.single_pane.position()
            if self._playing and self.single_pane.at_end():
                self._set_playing(False)
            if self.preview_mode == "processed" and time.monotonic() - self._last_live > 0.16:
                self._last_live = time.monotonic()
                self.preview_stack.setCurrentWidget(self.canvas)
                self._request_preview()
        else:
            return
        self.playhead = position / 1000
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(position))
            self.slider.blockSignals(False)
        self._update_time_label()

    def _stop_play(self) -> None:
        for pane in (self.single_pane, self.cmp_left, self.cmp_right):
            pane.pause()
        self._set_playing(False)

    def _stop_player(self) -> None:
        self._stop_play()

    def preview_clip(self) -> None:
        source = self._preview_source()
        info = self.media.get(str(source), {}) if source else None
        if source is None or not info:
            self.show_error("请先等视频信息读取完成。")
            return
        if self.thread and self.thread.isRunning():
            self.show_error("队列处理中，请先用静帧预览，避免两路编码抢占 CPU。")
            return
        if self.clip_thread and self.clip_thread.isRunning():
            return
        try:
            options = self._options_for(source)
        except ValueError as exc:
            self.show_error(str(exc))
            return
        if self._playing:
            self._pause_playback()
        duration = float(info["duration"])
        start = min(self.playhead, max(0.0, duration - 0.4))
        length = min(3.0, max(0.4, duration - start))
        # The players may still hold the previous clip open; release it and write a new file.
        self.single_pane.release()
        self.cmp_right.release()
        self._clip_serial += 1
        output = self.preview_dir / f"effect-clip-{self._clip_serial}.mp4"
        for old in self.preview_dir.glob("effect-clip*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass
        self.clip_button.setEnabled(False)
        self.preview_info.setText("正在渲染 3 秒处理后片段…")
        self.write_log(f"正在生成 3 秒效果片段（{fmt_time(start)} 起）…")
        self._pending_clip = {"source": str(source), "start": start, "length": length, "path": str(output)}
        self.clip_thread = ClipThread(source, info, options, start, length, output,
                                      self._ffmpeg_path(), self._preview_seed(source))
        self.clip_thread.ok.connect(self._on_clip_ok)
        self.clip_thread.fail.connect(self._on_clip_fail)
        self.clip_thread.start()

    def _on_clip_ok(self, path: str) -> None:
        self.clip_button.setEnabled(True)
        self.write_log(f"效果片段已生成：{path}")
        clip = dict(self._pending_clip)
        clip["path"] = path
        self.effect_clip = clip
        then, self._clip_then = self._clip_then, "compare"
        if not VideoPane.available():
            return
        if then == "single":
            self._play_single(path, clip["start"], clip["start"], "处理后效果片段（3 秒）")
        else:
            self._start_compare(path, clip["start"], clip["length"], "处理后 · 3 秒效果片段")

    def _on_clip_fail(self, message: str) -> None:
        self.clip_button.setEnabled(True)
        self._clip_then = "compare"
        self.show_error(message)

    def _start_compare(self, processed: str, start: float, length: float, label: str) -> None:
        source = self._preview_source()
        if source is None:
            return
        self.single_pane.pause()
        self.compare = {"start_ms": int(start * 1000), "length_ms": int(length * 1000), "path": processed}
        self.cmp_left.title.setText(f"原片 · {Path(source).name}")
        self.cmp_right.title.setText(f"{label} · 声音取自此侧")
        self.cmp_left.load(str(source), self.compare["start_ms"])
        self.cmp_right.load(processed, 0)
        self.view_mode = "compare"
        self.preview_stack.setCurrentWidget(self.compare_widget)
        self.playhead = start
        self.cmp_left.play()
        self.cmp_right.play()
        self._set_playing(True)
        self.preview_info.setText(f"并排对比 {fmt_time(start)}–{fmt_time(start + length)}，拖动进度条两侧同步")

    def compare_export(self) -> None:
        source = self._preview_source()
        if source is None:
            self.show_error("请先导入并选中原片。")
            return
        if not VideoPane.available():
            self.show_error("当前 Python 环境缺少 PySide6 QtMultimedia，无法播放。")
            return
        chosen = ""
        for job_id in self._selected_ids():
            spec = self.tasks.get(job_id)
            row = self.rows.get(job_id)
            item = self.task_table.item(row, 4) if row is not None else None
            if spec and self.states.get(job_id) == "success" and item and Path(item.text()).is_file():
                if str(spec.source) == str(source):
                    chosen = item.text()
                    break
        if not chosen:
            start_dir = self.output_edit.text().strip() or str(Path.home())
            chosen, _ = QFileDialog.getOpenFileName(self, "选择要对比的导出版本", start_dir,
                                                    "视频 (*.mp4 *.mov *.mkv);;所有文件 (*.*)")
        if not chosen:
            return
        if Path(chosen).resolve() == Path(source).resolve():
            self.show_error("请选择导出的版本，不是原片本身。")
            return
        duration = self._duration()
        self._start_compare(chosen, 0.0, duration or 1.0, f"导出版本 · {Path(chosen).name}")

    def _on_region(self, x: float, y: float, w: float, h: float) -> None:
        page = self.stack.currentIndex()
        source = self._preview_source()
        info = self.media.get(str(source), {}) if source else {}
        if page == 3:
            self._mask_preview = True
            self.mask_x.setValue(x * 100)
            self.mask_y.setValue(y * 100)
            self.mask_w.setValue(w * 100)
            self.mask_h.setValue(h * 100)
            sx, sy, sw, sh = adjacent_source(x, y, w, h)
            self.mask_sx.setValue(sx * 100)
            self.mask_sy.setValue(sy * 100)
            self.mask_sw.setValue(sw * 100)
            self.mask_sh.setValue(sh * 100)
            self.preview_info.setText("已框选，预览会显示这块蒙版。设好时间段；单条视频点「设为单独参数」。未点「添加蒙版」不会写入导出。")
            self._schedule_preview()
        elif page == 0 and self.crop_enabled.isChecked() and info:
            self.crop_x.setValue(int(x * info["width"]))
            self.crop_y.setValue(int(y * info["height"]))
            self.crop_w.setValue(max(16, int(w * info["width"])))
            self.crop_h.setValue(max(16, int(h * info["height"])))
            self.preview_info.setText("框选已填入裁剪像素。")
        else:
            self.preview_info.setText("框选蒙版请先打开「字幕蒙版」；框选裁剪请先勾选裁剪。")

    def _mask_from_form(self) -> MaskSpec:
        return MaskSpec(
            mode=str(self.mask_mode.currentData()),
            x=self.mask_x.value() / 100, y=self.mask_y.value() / 100,
            w=self.mask_w.value() / 100, h=self.mask_h.value() / 100,
            src_x=self.mask_sx.value() / 100, src_y=self.mask_sy.value() / 100,
            src_w=self.mask_sw.value() / 100, src_h=self.mask_sh.value() / 100,
            feather=self.mask_feather.value(), opacity=self.mask_opacity.value(),
            blur=self.mask_blur.value(), start=self.mask_start.value(), end=self.mask_end.value(),
        )

    def add_mask(self) -> None:
        try:
            mask = self._mask_from_form()
            validate_options(ProcessOptions(masks=(mask,)))
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.masks.append(mask)
        self._refresh_mask_list()
        self.mask_list.setCurrentRow(len(self.masks) - 1)
        self._on_form_changed()
        self.write_log(f"已添加蒙版，当前 {len(self.masks)} 段")

    def update_mask(self) -> None:
        row = self.mask_list.currentRow()
        if row < 0:
            self.show_error("请先在列表里选中一段蒙版。")
            return
        try:
            mask = self._mask_from_form()
            validate_options(ProcessOptions(masks=(mask,)))
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.masks[row] = mask
        self._refresh_mask_list()
        self.mask_list.setCurrentRow(row)
        self._on_form_changed()

    def delete_mask(self) -> None:
        row = self.mask_list.currentRow()
        if row < 0:
            return
        del self.masks[row]
        self._refresh_mask_list()
        self._on_form_changed()

    def _refresh_mask_list(self) -> None:
        self.mask_list.clear()
        names = dict(MASK_LABELS)
        for mask in self.masks:
            end = "片尾" if mask.end <= 0 else fmt_time(mask.end)
            self.mask_list.addItem(f"{names.get(mask.mode, mask.mode)}  {fmt_time(mask.start)}–{end}")

    def _load_mask_row(self, row: int) -> None:
        if row < 0 or row >= len(self.masks) or self._loading:
            return
        mask = self.masks[row]
        self._loading = True
        self._set_combo(self.mask_mode, mask.mode)
        self.mask_x.setValue(mask.x * 100)
        self.mask_y.setValue(mask.y * 100)
        self.mask_w.setValue(mask.w * 100)
        self.mask_h.setValue(mask.h * 100)
        self.mask_sx.setValue(mask.src_x * 100)
        self.mask_sy.setValue(mask.src_y * 100)
        self.mask_sw.setValue(mask.src_w * 100)
        self.mask_sh.setValue(mask.src_h * 100)
        self.mask_feather.setValue(mask.feather)
        self.mask_opacity.setValue(mask.opacity)
        self.mask_blur.setValue(mask.blur)
        self.mask_start.setValue(mask.start)
        self.mask_end.setValue(mask.end)
        self._loading = False

    def save_override(self) -> None:
        selected = self._selected_sources() or ([self._preview_source()] if self._preview_source() else [])
        if not selected:
            self.show_error("请先选中素材。")
            return
        try:
            options = self.collect_options()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        for path in selected:
            self.overrides[str(path)] = options.to_dict()
        self._refresh_source_list()
        self.write_log(f"已把当前参数单独设给 {len(selected)} 个素材。之后改右侧设置，不会再跟着变，除非清除。")

    def clear_override(self) -> None:
        for path in self._selected_sources():
            self.overrides.pop(str(path), None)
        self._refresh_source_list()

    def _selected_sources(self) -> list[Path]:
        return [Path(item.data(Qt.UserRole)) for item in self.source_list.selectedItems()]

    def _sources_for_run(self) -> list[Path]:
        if self.mode == "single":
            selected = self._selected_sources()
            if len(selected) == 1:
                return selected
            if len(self.sources) == 1:
                return list(self.sources)
            raise ValueError("单视频模式请选中一条素材。一次处理多条请切换到批量素材。")
        if not self.sources:
            raise ValueError("请先导入至少一个视频")
        return list(self.sources)

    def _options_for(self, source: Path) -> ProcessOptions:
        raw = self.overrides.get(str(source))
        options = ProcessOptions.from_dict(raw) if raw else self.collect_options()
        if self.mode == "film":
            options = self._strip_film(options)
        validate_options(options)
        info = self.media.get(str(source))
        if info:
            validate_against_media(options, int(info["width"]), int(info["height"]))
        return options

    def _preset_for_form(self) -> Preset:
        data = self.preset_choice.currentData()
        if isinstance(data, tuple) and data[0] == "builtin":
            try:
                return Preset.from_file(data[1])
            except (OSError, ValueError):
                pass
        return Preset(self.preset_choice.currentText() or "自定义", crf=self.crf.value(),
                      encoder_preset=str(self.encoder_preset.currentData() or "medium"))

    def _preview_seed(self, source: Path) -> int:
        job = JobSpec(source.resolve(), source.parent, 1, self.seed.value(), Preset("预览"))
        return job.derived_seed()

    def enqueue_sources(self) -> None:
        if self.thread and self.thread.isRunning():
            self.show_error("队列运行中不能追加任务，请等这一批结束。")
            return
        try:
            sources = self._sources_for_run()
            output = self.output_edit.text().strip()
            if not output:
                raise ValueError("请填写导出目录")
            plans = [(source, self._options_for(source)) for source in sources]
        except ValueError as exc:
            self.show_error(str(exc))
            return
        warnings = []
        for _source, options in plans:
            for line in risky_warnings(options):
                if line not in warnings:
                    warnings.append(line)
        if warnings:
            answer = QMessageBox.question(
                self, "这些操作会改变画面",
                "以下操作会真正执行：\n\n" + "\n".join(f"· {line}" for line in warnings) + "\n\n继续加入队列？")
            if answer != QMessageBox.Yes:
                return
        preset = self._preset_for_form()
        try:
            specs = []
            for source, options in plans:
                specs.extend(make_jobs([source], Path(output), self.versions.value(), self.seed.value(),
                                       preset, self.full_verify.isChecked(), options))
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        for spec in specs:
            self._add_row(spec)
        names = enabled_summary(plans[0][1])
        self.write_log(f"已加入 {len(specs)} 项。启用：{'、'.join(names)}")

    def _add_row(self, spec: JobSpec) -> None:
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        self.tasks[spec.job_id] = spec
        self.rows[spec.job_id] = row
        self.states[spec.job_id] = "queued"
        for column, value in ((0, spec.source.name), (1, f"V{spec.version:03d}"), (2, "等待中"), (4, "—"), (5, spec.job_id)):
            self.task_table.setItem(row, column, QTableWidgetItem(value))
        bar = QProgressBar()
        bar.setValue(0)
        self.task_table.setCellWidget(row, 3, bar)
        self.task_table.setRowHeight(row, 30)
        self._update_count()

    def _update_count(self) -> None:
        total = len(self.tasks)
        success = sum(state == "success" for state in self.states.values())
        failed = sum(state == "failed" for state in self.states.values())
        self.count_label.setText(f"{total} 项 · 完成 {success} · 失败 {failed}")
        self.statusBar().showMessage(f"队列 {total} 项 · 完成 {success} · 失败 {failed}")

    def _selected_ids(self) -> list[str]:
        return [self.task_table.item(index.row(), 5).text()
                for index in self.task_table.selectionModel().selectedRows()]

    def _ffmpeg_path(self) -> str:
        return self.ffmpeg_edit.text().strip() or "ffmpeg"

    def _ffprobe_path(self) -> str:
        return self.ffprobe_edit.text().strip() or "ffprobe"

    def check_environment(self) -> None:
        try:
            ffmpeg = resolve_executable(self._ffmpeg_path(), "ffmpeg")
            ffprobe = resolve_executable(self._ffprobe_path(), "ffprobe")
        except OSError as exc:
            self.show_error(str(exc))
            self.env_label.setText("未找到 FFmpeg")
            return
        found = list_video_encoders(ffmpeg)
        self._fill_codecs(found)
        font = find_cjk_font()
        self.write_log(f"FFmpeg：{ffmpeg}")
        self.write_log(f"FFprobe：{ffprobe}")
        self.write_log("编码器：" + ("、".join(sorted(found)) if found else "未识别"))
        if font:
            self.write_log(f"中文字体：{font}")
        self.env_label.setText("FFmpeg 就绪")
        QMessageBox.information(self, "环境检查", "FFmpeg 和 FFprobe 都可以使用。")

    def _check_env_silent(self) -> None:
        try:
            ffmpeg = resolve_executable(self._ffmpeg_path(), "ffmpeg")
            resolve_executable(self._ffprobe_path(), "ffprobe")
        except OSError:
            self.env_label.setText("未找到 FFmpeg")
            self.statusBar().showMessage("未找到 FFmpeg · 可在导出页配置路径")
            return
        self._fill_codecs(list_video_encoders(ffmpeg))
        self.env_label.setText("FFmpeg 就绪")
        self.statusBar().showMessage("就绪 · 不覆盖原片")

    def _fill_codecs(self, found: set[str]) -> None:
        current = self.codec.currentData()
        self._loading = True
        self.codec.clear()
        labels = {"libx264": "H.264", "libx265": "H.265",
                  "h264_nvenc": "NVIDIA H.264（需显卡）", "hevc_nvenc": "NVIDIA H.265（需显卡）"}
        available = [name for name in labels if not found or name in found]
        if not available:
            available = ["libx264"]
        for name in available:
            self.codec.addItem(labels[name], name)
        self._set_combo(self.codec, current or "libx264")
        self._loading = False

    def start_queue(self) -> None:
        if self.thread and self.thread.isRunning():
            self.show_error("队列正在运行。")
            return
        pending = [spec for job_id, spec in self.tasks.items() if self.states[job_id] == "queued"]
        if not pending:
            self.show_error("没有等待中的任务。请先导入视频并加入队列。")
            return
        try:
            resolve_executable(self._ffmpeg_path(), "ffmpeg")
            resolve_executable(self._ffprobe_path(), "ffprobe")
        except OSError as exc:
            self.show_error(str(exc))
            return
        self.thread = QueueThread(pending, self._ffmpeg_path(), self._ffprobe_path())
        self.thread.event_received.connect(self.on_queue_event)
        self.thread.finished.connect(self.on_queue_finished)
        self.is_paused = False
        self.pause_btn.setText("暂停")
        self.start_button.setEnabled(False)
        self.env_label.setText("正在处理")
        self.write_log(f"开始处理 {len(pending)} 项")
        self.show_bottom(0)
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
        if event.parameters:
            self.results[event.job_id] = event.parameters
        if event.status in {"failed", "success", "cancelled", "retrying", "paused"}:
            spec = self.tasks[event.job_id]
            self.write_log(f"{spec.source.name} V{spec.version:03d} — {event.message}")
            if event.status in {"failed", "success", "cancelled"} and self.store:
                try:
                    self.store.add_task(str(spec.source), spec.version, event.status,
                                        str(event.output) if event.output else None, event.message, event.parameters)
                except Exception:
                    pass
        self._update_count()

    def on_queue_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.pause_btn.setText("暂停")
        self.is_paused = False
        self.env_label.setText("FFmpeg 就绪")
        self.write_log("本批队列已结束。")
        self.thread = None
        self._update_count()

    def toggle_pause(self) -> None:
        if not self.thread or not self.thread.isRunning():
            self.show_error("当前没有在运行的队列。")
            return
        try:
            if self.is_paused:
                self.thread.runner.resume()
                self.is_paused = False
                self.pause_btn.setText("暂停")
                self.write_log("队列继续")
            else:
                self.thread.runner.pause()
                self.is_paused = True
                self.pause_btn.setText("继续")
                self.write_log("队列已暂停，当前 FFmpeg 进程也已暂停")
        except Exception as exc:
            self.show_error(f"暂停或继续失败：{exc}")

    def _cancel(self, ids: list[str]) -> None:
        active = [job_id for job_id in ids if self.states.get(job_id) in {"queued", "running", "paused", "retrying"}]
        if not active:
            return
        if self.thread and self.thread.isRunning():
            self.thread.runner.cancel(active)
        else:
            for job_id in active:
                self.on_queue_event(QueueEvent(job_id, "cancelled", 0, "用户取消"))
        self.write_log(f"已请求取消 {len(active)} 项")

    def cancel_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self.show_error("请先在队列里选中要取消的任务。")
        else:
            self._cancel(ids)

    def cancel_all(self) -> None:
        self._cancel(list(self.tasks))
        if self.thread and self.thread.isRunning() and self.is_paused:
            self.thread.runner.resume()
            self.is_paused = False
            self.pause_btn.setText("暂停")

    def retry_selected(self) -> None:
        if self.thread and self.thread.isRunning():
            self.show_error("请等当前队列结束后再重试。")
            return
        ids = self._selected_ids() or list(self.tasks)
        count = 0
        for job_id in ids:
            if self.states.get(job_id) in {"failed", "cancelled"}:
                self.on_queue_event(QueueEvent(job_id, "queued", 0, "重新排队"))
                self.task_table.item(self.rows[job_id], 4).setText("—")
                count += 1
        self.write_log(f"重新排队 {count} 项")

    def show_parameters(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            self.show_error("请选中一条任务再查看参数。")
            return
        job_id = ids[0]
        spec = self.tasks[job_id]
        used = self.results.get(job_id)
        lines = [f"文件：{spec.source}", f"版本：{spec.version}    种子：{spec.seed}", ""]
        if used:
            lines.append("这一版实际使用的参数：")
            lines.append(json.dumps(used, ensure_ascii=False, indent=2))
        elif spec.options:
            lines.append("尚未处理。加入队列时启用的是：")
            lines.extend(enabled_summary(spec.options))
        else:
            lines.append("这一项使用内置预设的调色范围，处理完成后可在这里看到实际数值。")
        dialog = QDialog(self)
        dialog.setWindowTitle("任务参数")
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        edit = QPlainTextEdit("\n".join(lines))
        edit.setReadOnly(True)
        layout.addWidget(edit)
        dialog.exec()

    def show_history(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("任务记录")
        dialog.resize(720, 360)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["时间", "素材", "版本", "状态", "说明"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        rows = []
        if self.store:
            try:
                rows = self.store.recent_tasks(40)
            except Exception as exc:
                self.write_log(f"读取记录失败：{exc}")
        for record in rows:
            row = table.rowCount()
            table.insertRow(row)
            values = [record.get("created", ""), Path(record.get("source", "")).name,
                      str(record.get("version") or ""), STATE_LABELS.get(record.get("status", ""), record.get("status", "")),
                      record.get("message", "")]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        if not rows:
            layout.addWidget(QLabel("还没有完成的任务记录。"))
        layout.addWidget(table)
        dialog.exec()

    def save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "保存预设", "预设名称")
        if not ok:
            return
        name = name.strip()
        if not name or any(char in name for char in "\\/:*?\"<>|"):
            self.show_error("预设名称无效。")
            return
        try:
            options = self.collect_options()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        payload = {"kind": "yingxu-profile", "version": 1, "name": name, "options": options.to_dict()}
        path = user_preset_dir() / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.store:
            try:
                self.store.save_preset(name, payload)
            except Exception as exc:
                self.write_log(f"预设已写入文件，数据库副本失败：{exc}")
        self._load_presets()
        index = self.preset_choice.findText(name)
        if index >= 0:
            self.preset_choice.setCurrentIndex(index)
        self.write_log(f"已保存预设：{path}")

    def delete_preset(self) -> None:
        data = self.preset_choice.currentData()
        if not isinstance(data, tuple) or data[0] != "profile":
            self.show_error("只能删除你自己保存的预设。")
            return
        path = Path(data[1])
        path.unlink(missing_ok=True)
        if self.store:
            try:
                self.store.delete_preset(path.stem)
            except Exception:
                pass
        self._load_presets()
        self.write_log("已删除自定义预设")

    def save_project(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "保存项目", str(Path.home() / "影序项目.json"), "JSON (*.json)")
        if not chosen:
            return
        try:
            options = self.collect_options()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        payload = {
            "kind": "yingxu-project", "version": 1, "mode": self.mode,
            "sources": [str(path) for path in self.sources], "versions": self.versions.value(),
            "seed": self.seed.value(), "output": self.output_edit.text(),
            "verify_full": self.full_verify.isChecked(), "preset": self.preset_choice.currentText(),
            "options": options.to_dict(), "overrides": self.overrides,
            "ffmpeg": self.ffmpeg_edit.text(), "ffprobe": self.ffprobe_edit.text(),
        }
        Path(chosen).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.write_log(f"项目已保存：{chosen}")

    def load_project(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "打开项目", str(Path.home()), "JSON (*.json)")
        if not chosen:
            return
        try:
            payload = json.loads(Path(chosen).read_text(encoding="utf-8"))
            options = ProcessOptions.from_dict(payload["options"])
        except (OSError, ValueError, KeyError) as exc:
            self.show_error(f"项目无法打开：{exc}")
            return
        self.sources = []
        self.media.clear()
        missing = 0
        for raw in payload.get("sources") or []:
            path = Path(raw)
            if path.is_file():
                self.sources.append(path.resolve())
                self._queue_probe(str(path.resolve()))
            else:
                missing += 1
        self.overrides = {key: value for key, value in (payload.get("overrides") or {}).items() if isinstance(value, dict)}
        self._loading = True
        self.versions.setValue(int(payload.get("versions") or 1))
        self.seed.setValue(int(payload.get("seed") or 0))
        self.output_edit.setText(str(payload.get("output") or ""))
        self.full_verify.setChecked(bool(payload.get("verify_full", True)))
        self.ffmpeg_edit.setText(str(payload.get("ffmpeg") or "ffmpeg"))
        self.ffprobe_edit.setText(str(payload.get("ffprobe") or "ffprobe"))
        mode = payload.get("mode") if payload.get("mode") in {"single", "batch", "film"} else "single"
        for button in self.mode_group.buttons():
            if button.property("mode") == mode:
                button.setChecked(True)
        self._loading = False
        self.apply_options(options)
        self._set_mode(mode)
        self._mark_custom()
        self._refresh_source_list()
        self.write_log(f"已打开项目：{chosen}" + (f"，{missing} 个文件不存在" if missing else ""))

    def closeEvent(self, event) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "任务运行中", "请先取消全部任务并等待队列结束，再关闭窗口。")
            event.ignore()
            return
        for pane in (self.single_pane, self.cmp_left, self.cmp_right):
            pane.release()
        for worker in (self.preview_thread, self.clip_thread, self.probe_thread):
            if worker and worker.isRunning():
                worker.wait(3000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    font = QFont("Microsoft YaHei UI", 10)
    if not font.exactMatch():
        font = QFont("WenQuanYi Micro Hei", 10)
    app.setFont(font)
    window = MainWindow()
    if "--windowed" in sys.argv:
        window.show()
    else:
        window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
