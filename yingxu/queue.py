"""Reusable sequential queue with pause, cancellation, progress, and retries."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .engine import JobCancelled, ProcessControl, run_job
from .models import JobSpec


@dataclass(frozen=True)
class QueueEvent:
    job_id: str
    status: str
    percent: int
    message: str
    attempt: int = 0
    output: Path | None = None
    parameters: dict | None = None


class BatchRunner:
    """Call run() in a worker thread (GUI) or foreground (CLI)."""

    def __init__(self, jobs: list[JobSpec], on_event: Callable[[QueueEvent], None] | None = None,
                 retries: int = 0, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe") -> None:
        if retries < 0 or retries > 10:
            raise ValueError("重试次数必须在0～10之间")
        self.jobs = list(jobs)
        self.on_event = on_event or (lambda e: None)
        self.retries = retries
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._lock = threading.RLock()
        self._resume = threading.Event()
        self._resume.set()
        self._cancel_ids: set[str] = set()
        self._active_id: str | None = None
        self._active_control: ProcessControl | None = None
        self.results: dict[str, QueueEvent] = {}

    def _emit(self, e: QueueEvent) -> None:
        self.results[e.job_id] = e
        self.on_event(e)

    def pause(self) -> None:
        with self._lock:
            if not self._resume.is_set():
                return
            if self._active_control:
                self._active_control.pause()
            self._resume.clear()
            if self._active_id:
                latest = self.results.get(self._active_id)
                self._emit(QueueEvent(self._active_id, "paused", latest.percent if latest else 0, "已暂停"))

    def resume(self) -> None:
        with self._lock:
            if self._resume.is_set():
                return
            if self._active_control:
                self._active_control.resume()
            self._resume.set()
            if self._active_id:
                latest = self.results.get(self._active_id)
                self._emit(QueueEvent(self._active_id, "running", latest.percent if latest else 0, "继续处理"))

    def cancel(self, job_ids: list[str]) -> None:
        with self._lock:
            self._cancel_ids.update(job_ids)
            if self._active_id in self._cancel_ids and self._active_control:
                self._active_control.cancel()
            # Preserve the global paused state when only cancelling queued items.
            # The caller can resume (or cancel the whole queue) explicitly.

    def run(self) -> dict[str, QueueEvent]:
        for job in self.jobs:
            self._resume.wait()
            with self._lock:
                if job.job_id in self._cancel_ids:
                    self._emit(QueueEvent(job.job_id, "cancelled", 0, "已取消（未执行）"))
                    continue
                control = ProcessControl()
                self._active_id = job.job_id
                self._active_control = control
            try:
                for attempt in range(1, self.retries + 2):
                    if job.job_id in self._cancel_ids:
                        raise JobCancelled("已取消")
                    self._emit(QueueEvent(job.job_id, "running", 0, "启动任务", attempt))

                    def update(percent: int, msg: str) -> None:
                        latest = self.results.get(job.job_id)
                        if latest and latest.status == "paused":
                            return
                        self._emit(QueueEvent(job.job_id, "running", percent, msg, attempt))

                    try:
                        result = run_job(job, progress=update, control=control,
                                         ffmpeg_path=self.ffmpeg_path, ffprobe_path=self.ffprobe_path)
                        self._emit(QueueEvent(job.job_id, "success", 100, "完成", attempt,
                                              result.output, result.parameters))
                        break
                    except JobCancelled:
                        self._emit(QueueEvent(job.job_id, "cancelled", 0, "已取消", attempt))
                        break
                    except Exception as exc:
                        if attempt <= self.retries:
                            self._emit(QueueEvent(job.job_id, "retrying", 0,
                                                  f"第{attempt}次失败，准备重试：{exc}", attempt))
                        else:
                            self._emit(QueueEvent(job.job_id, "failed", 0, str(exc), attempt))
            finally:
                with self._lock:
                    self._active_id = None
                    self._active_control = None
        return self.results
