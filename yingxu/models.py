"""Shared configuration and immutable job definitions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class Preset:
    name: str
    brightness: tuple[float, float] = (0.0, 0.0)
    contrast: tuple[float, float] = (1.0, 1.0)
    saturation: tuple[float, float] = (1.0, 1.0)
    audio_gain_db: tuple[float, float] = (0.0, 0.0)
    crf: int = 19
    encoder_preset: str = "medium"

    @classmethod
    def from_file(cls, path: str | Path) -> Preset:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = {"name", "brightness", "contrast", "saturation", "audio_gain_db", "crf", "encoder_preset"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"预设中存在未知字段: {sorted(unknown)}")
        ranges = {}
        limits = {"brightness": (-0.25, 0.25), "contrast": (0.5, 2.0),
                  "saturation": (0.0, 3.0), "audio_gain_db": (-18.0, 18.0)}
        for key, (lower, upper) in limits.items():
            values = data.get(key, list(getattr(cls(name="temp"), key)))
            if not isinstance(values, list) or len(values) != 2:
                raise ValueError(f"{key} 必须是包含两个数字的数组")
            a, b = float(values[0]), float(values[1])
            if not (lower <= a <= b <= upper):
                raise ValueError(f"{key} 超出允许范围或最小值大于最大值")
            ranges[key] = (a, b)
        crf = int(data.get("crf", 19))
        enc = str(data.get("encoder_preset", "medium"))
        if not 14 <= crf <= 30 or enc not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}:
            raise ValueError("CRF 或编码速度预设无效")
        return cls(name=str(data.get("name", Path(path).stem)), crf=crf,
                   encoder_preset=enc, **ranges)


def preset_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "presets"


@dataclass(frozen=True)
class JobSpec:
    source: Path
    output_dir: Path
    version: int
    seed: int
    preset: Preset
    verify_full: bool = True
    job_id: str = field(default_factory=lambda: uuid4().hex)

    def derived_seed(self) -> int:
        content = f"{self.seed}\0{self.source.resolve()}\0{self.version}".encode("utf-8")
        return int.from_bytes(hashlib.sha256(content).digest()[:8], "big")


def make_jobs(sources: list[Path], output_dir: Path, versions: int,
              seed: int, preset: Preset, verify_full: bool = True) -> list[JobSpec]:
    if not 1 <= versions <= 1000:
        raise ValueError("版本数量必须在1至1000之间")
    if not sources:
        raise ValueError("请先导入至少一个视频")
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"找不到源视频: {source}")
        if source.parent.resolve() == output_dir.resolve():
            raise ValueError(f"导出目录不能直接等于源视频所在目录: {source}")
    return [JobSpec(source=s.resolve(), output_dir=output_dir.resolve(),
                    version=v, seed=seed, preset=preset, verify_full=verify_full)
            for s in sources for v in range(1, versions + 1)]
