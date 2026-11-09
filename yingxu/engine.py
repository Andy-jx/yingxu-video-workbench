"""Safe FFmpeg execution and media validation; no UI dependencies."""
from __future__ import annotations

import errno
import json
import os
import random
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psutil

from .models import JobSpec

ProgressCallback = Callable[[int, str], None]


class JobCancelled(Exception):
    pass


class MediaError(RuntimeError):
    pass


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resolve_executable(value: str, default_name: str) -> str:
    """Accept an executable, directory containing it, or a PATH command."""
    candidate = Path(value).expanduser() if value else Path(default_name)
    if candidate.is_dir():
        candidate = candidate / (default_name + (".exe" if os.name == "nt" else ""))
    found = shutil.which(str(candidate))
    if not found:
        raise FileNotFoundError(f"找不到 {default_name}: {value or default_name}，请配置 FFmpeg 路径")
    return found


def probe_media(path: Path, ffprobe: str) -> dict:
    command = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120, creationflags=_flags())
    if result.returncode:
        raise MediaError(f"FFprobe 读取失败: {result.stderr[-800:]}")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("FFprobe 返回了无效 JSON") from exc
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise MediaError(f"找不到可用的视频轨道: {path.name}")
    video = videos[0]
    duration = info.get("format", {}).get("duration") or video.get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise MediaError("无法读取视频时长") from exc
    if not 0.01 <= duration < 7 * 24 * 3600:
        raise MediaError("视频时长无效或超过7天限制")
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    if width < 2 or height < 2:
        raise MediaError(f"无法读取视频分辨率: {path.name}")
    return {"duration": duration, "video": video, "width": width, "height": height,
            "fps": _frame_rate(video),
            "audio_count": sum(s.get("codec_type") == "audio" for s in info.get("streams", []))}


def _frame_rate(video: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = str(video.get(key) or "")
        if "/" not in raw:
            continue
        num, den = raw.split("/", 1)
        try:
            den_f = float(den)
            if den_f:
                value = float(num) / den_f
                if value > 1:
                    return value
        except ValueError:
            continue
    return 25.0


class ProcessControl:
    """Thread-safe controls for a currently running FFmpeg child process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._paused = False
        self._cancel = False

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancel

    def _suspend(self, proc: subprocess.Popen, suspend: bool) -> None:
        if proc.poll() is None:
            try:
                operation = psutil.Process(proc.pid).suspend if suspend else psutil.Process(proc.pid).resume
                operation()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
                raise MediaError(f"{'暂停' if suspend else '继续'}FFmpeg 失败: {exc}") from exc

    def attach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._proc = proc
            if self._cancel:
                proc.terminate()
            elif self._paused:
                self._suspend(proc, True)

    def detach(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self._proc is proc:
                self._proc = None

    def pause(self) -> None:
        with self._lock:
            if self._cancel or self._paused:
                return
            if self._proc:
                self._suspend(self._proc, True)
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            if not self._paused:
                return
            if self._proc:
                self._suspend(self._proc, False)
            self._paused = False

    def cancel(self) -> None:
        with self._lock:
            self._cancel = True
            if self._proc and self._proc.poll() is None:
                if self._paused:
                    try:
                        self._suspend(self._proc, False)
                    except MediaError:
                        pass
                self._proc.terminate()
            self._paused = False

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled("任务已取消")


def _reject_black_output(ffmpeg: str, source: Path, output: Path, src_duration: float,
                         out_duration: float, values: dict) -> None:
    """Fail only when a geometry filter turns a visible frame almost black."""
    sampled = values.get("values") or {}
    scale = sampled.get("scale")
    risky = ("rotate" in sampled or "crop" in values or (scale is not None and scale < 0.999))
    if not risky:
        return
    from .processing import frame_luma
    src_y = frame_luma(ffmpeg, source, src_duration * 0.4)
    out_y = frame_luma(ffmpeg, output, max(0.0, out_duration * 0.4))
    if src_y is not None and out_y is not None and out_y < 4 and src_y > 20:
        raise MediaError(f"导出画面接近黑屏（亮度 {out_y:.1f}）。请减小旋转、裁剪或缩小后再试。")


def _parse_time(value: str) -> float:
    try:
        h, m, s = value.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, TypeError):
        return 0.0


def _ffmpeg_run(command: list[str], control: ProcessControl, duration: float,
                start_pct: int, end_pct: int, stage: str, update: ProgressCallback) -> None:
    """Drain progress pipe and store error output in a disk-backed temporary file."""
    import tempfile

    control.check()
    with tempfile.TemporaryFile(mode="w+b") as error_log:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_log,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, creationflags=_flags())
        try:
            control.attach(proc)
            assert proc.stdout is not None
            for line in proc.stdout:
                control.check()
                if line.startswith("out_time="):
                    t = _parse_time(line.partition("=")[2])
                    pct = start_pct + int((end_pct - start_pct) * min(1, t / duration))
                    update(min(pct, end_pct), stage)
            rc = proc.wait()
            control.check()
            if rc != 0:
                error_log.seek(0)
                tail = error_log.read()[-3000:].decode("utf-8", errors="replace")
                raise MediaError(f"FFmpeg 返回错误码 {rc}: {tail.strip()}")
        finally:
            control.detach(proc)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout:
                proc.stdout.close()


@dataclass
class JobResult:
    output: Path
    parameters: dict


def _select_parameters(job: JobSpec) -> dict:
    rng = random.Random(job.derived_seed())
    p = job.preset
    return {name: round(rng.uniform(*getattr(p, name)), 5)
            for name in ("brightness", "contrast", "saturation", "audio_gain_db")}


def _make_filters(values: dict) -> tuple[list[str], list[str]]:
    v = []
    if (abs(values["brightness"]) > 0.000001 or
            abs(values["contrast"] - 1) > 0.000001 or
            abs(values["saturation"] - 1) > 0.000001):
        v = ["-vf", (f"eq=brightness={values['brightness']:.5f}:"
                     f"contrast={values['contrast']:.5f}:"
                     f"saturation={values['saturation']:.5f}")]
    a = ["-af", f"volume={values['audio_gain_db']:.5f}dB"] if abs(values["audio_gain_db"]) > 0.000001 else []
    return v, a


def _publish_no_overwrite(temp: Path, directory: Path, stem: str) -> Path:
    """Create a final path without replacing an existing file, even on collision."""
    for suffix in range(10000):
        target = directory / f"{stem}{'_' + str(suffix).zfill(3) if suffix else ''}.mp4"
        try:
            os.link(temp, target)  # Atomic on normal local NTFS / ext filesystems.
            temp.unlink()
            return target
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno not in {errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EACCES}:
                raise
            # Filesystem without hard links: create exclusively, then stream copy.
            try:
                with target.open("xb") as dest, temp.open("rb") as src:
                    shutil.copyfileobj(src, dest, length=1024 * 1024)
                    dest.flush()
                    os.fsync(dest.fileno())
                temp.unlink()
                return target
            except FileExistsError:
                continue
            except Exception:
                target.unlink(missing_ok=True)
                raise
    raise MediaError("导出文件名冲突次数过多，请更换输出目录")


def run_job(job: JobSpec, progress: ProgressCallback | None = None,
            control: ProcessControl | None = None,
            ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe") -> JobResult:
    """Encode to a temporary file, validate, then atomically publish without clobbering."""
    update = progress or (lambda percent, message: None)
    control = control or ProcessControl()
    control.check()
    ffmpeg = resolve_executable(ffmpeg_path, "ffmpeg")
    ffprobe = resolve_executable(ffprobe_path, "ffprobe")
    source = job.source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"原始文件不存在: {source}")
    dest = job.output_dir.resolve()
    if dest == source.parent:
        raise MediaError("导出文件夹不能直接等于源视频所在文件夹")
    src = probe_media(source, ffprobe)
    control.check()
    dest.mkdir(parents=True, exist_ok=True)
    temp = dest / f".yingxu_{uuid.uuid4().hex}.tmp.mp4"
    output_base = f"{source.stem}_v{job.version:03d}"
    if job.options is None:
        values = _select_parameters(job)
        video_filters, audio_filters = _make_filters(values)
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                   "-i", str(source), "-map", f"0:{src['video']['index']}", "-map", "0:a?",
                   *video_filters, "-c:v", "libx264", "-preset", job.preset.encoder_preset,
                   "-crf", str(job.preset.crf), "-pix_fmt", "yuv420p",
                   *audio_filters, "-c:a", "aac", "-b:a", "192k",
                   "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
                   "-f", "mp4", str(temp)]
        expected_duration = src["duration"]
        verify_full = job.verify_full
    else:
        from .processing import prepare_encode
        command, values, expected_duration, force_verify = prepare_encode(job, src, ffmpeg, temp)
        verify_full = job.verify_full or force_verify
    try:
        update(0, "开始编码")
        _ffmpeg_run(command, control, expected_duration, 1, 88, "编码中", update)
        control.check()
        update(90, "检查媒体信息")
        out = probe_media(temp, ffprobe)
        if src["audio_count"] and not out["audio_count"]:
            raise MediaError("导出文件丢失了音频轨道")
        tolerance = max(0.5, src["duration"] * 0.02) if job.options is None else max(0.35, expected_duration * 0.04)
        if abs(expected_duration - out["duration"]) > tolerance:
            raise MediaError(f"导出时长不一致: 预期{expected_duration:.3f}s / 输出{out['duration']:.3f}s")
        if job.options is not None:
            _reject_black_output(ffmpeg, source, temp, src["duration"], out["duration"], values)
        if verify_full:
            command = [ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-nostdin",
                       "-i", str(temp), "-map", "0:v:0", "-map", "0:a?",
                       "-progress", "pipe:1", "-nostats", "-f", "null", "-"]
            _ffmpeg_run(command, control, out["duration"], 91, 99, "完整解码校验", update)
        control.check()
        final = _publish_no_overwrite(temp, dest, output_base)
        update(100, "导出成功")
        return JobResult(final, values)
    finally:
        temp.unlink(missing_ok=True)
