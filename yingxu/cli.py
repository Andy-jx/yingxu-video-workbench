"""`python -m yingxu.cli process ...`"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import MediaError, resolve_executable
from .models import Preset, make_jobs, preset_dir
from .queue import BatchRunner, QueueEvent

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".ts"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yingxu", description="影序·视频工作台（V0）CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process", help="一个或多个视频生成多个版本")
    p.add_argument("--input", nargs="+", type=Path, metavar="VIDEO", help="一个或多个视频路径")
    p.add_argument("--input-dir", type=Path, help="批量扫描此文件夹中的视频（非递归）")
    p.add_argument("--output", required=True, type=Path, help="输出文件夹（不得直接等于源文件夹）")
    p.add_argument("--versions", type=int, default=1, help="每个视频生成的版本数量，1-1000")
    p.add_argument("--seed", type=int, default=2026, help="随机种子，固定后参数可复现")
    p.add_argument("--preset", type=Path, default=preset_dir() / "标准处理.json", help="预设JSON路径")
    p.add_argument("--retries", type=int, default=1, help="失败时自动重试次数，默认1")
    p.add_argument("--verify", choices=["probe", "full"], default="full", help="probe检查轨道时长；full额外完整解码，默认full")
    p.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg可执行文件路径或包含它的文件夹")
    p.add_argument("--ffprobe", default="ffprobe", help="ffprobe可执行文件路径或包含它的文件夹")
    p.add_argument("--report", type=Path, help="可选：写入JSON任务报告")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        resolve_executable(args.ffmpeg, "ffmpeg")
        resolve_executable(args.ffprobe, "ffprobe")
        sources = list(args.input or [])
        if args.input_dir:
            if not args.input_dir.is_dir():
                raise FileNotFoundError(f"素材目录不存在: {args.input_dir}")
            sources.extend(p for p in sorted(args.input_dir.iterdir())
                           if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
        sources = list(dict.fromkeys(p.resolve() for p in sources))
        preset = Preset.from_file(args.preset)
        jobs = make_jobs(sources, args.output, args.versions, args.seed, preset, args.verify == "full")
    except (ValueError, OSError, MediaError) as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    def on_event(event: QueueEvent) -> None:
        print(f"[{event.status:9}] {event.job_id[:8]} {event.percent:3}%  {event.message}", flush=True)

    runner = BatchRunner(jobs, on_event=on_event, retries=args.retries,
                         ffmpeg_path=args.ffmpeg, ffprobe_path=args.ffprobe)
    try:
        results = runner.run()
    except KeyboardInterrupt:
        runner.cancel([j.job_id for j in jobs])
        print("收到中断，已请求取消", file=sys.stderr)
        return 130
    success = sum(e.status == "success" for e in results.values())
    print(f"完成：成功{success}/{len(jobs)}，失败或取消{len(jobs) - success}")
    if args.report:
        report = [{"job_id": j.job_id, "source": str(j.source), "version": j.version,
                   "seed": j.seed, "derived_seed": j.derived_seed(),
                   "status": results[j.job_id].status, "attempts": results[j.job_id].attempt,
                   "output": str(results[j.job_id].output) if results[j.job_id].output else None,
                   "parameters": results[j.job_id].parameters, "message": results[j.job_id].message}
                  for j in jobs]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"任务报告：{args.report}")
    return 0 if success == len(jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
