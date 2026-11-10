"""Shared configuration and immutable job definitions."""
from __future__ import annotations

import hashlib
import json
import random
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


WATERMARK_STYLES = (
    "text", "image", "translucent", "tile", "diagonal", "move_h", "move_v", "path",
)
MASK_MODES = ("blur", "copy", "flip_h", "flip_v")
VIDEO_CODECS = ("libx264", "libx265", "h264_nvenc", "hevc_nvenc")
ENCODER_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow")


@dataclass(frozen=True)
class RangeSpec:
    """Inclusive random range. Disabled ranges are never applied."""

    enabled: bool = False
    low: float = 0.0
    high: float = 0.0

    def sample(self, rng: random.Random) -> float:
        low, high = (self.low, self.high) if self.low <= self.high else (self.high, self.low)
        if abs(high - low) < 1e-12:
            return float(low)
        return float(rng.uniform(low, high))

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, data: object, default: RangeSpec | None = None) -> RangeSpec:
        base = default or cls()
        if not isinstance(data, dict):
            return base
        return cls(
            enabled=bool(data.get("enabled", base.enabled)),
            low=float(data.get("low", base.low)),
            high=float(data.get("high", base.high)),
        )


@dataclass(frozen=True)
class WatermarkSpec:
    enabled: bool = False
    style: str = "text"
    text: str = ""
    image: str = ""
    x: float = 0.04
    y: float = 0.06
    size: int = 36
    opacity: float = 0.30
    angle: float = 0.0
    speed: float = 0.0
    start: float = 0.0
    end: float = 0.0
    spacing: int = 240
    path_points: tuple[tuple[float, float], ...] = ()
    font: str = ""

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled, "style": self.style, "text": self.text, "image": self.image,
            "x": self.x, "y": self.y, "size": self.size, "opacity": self.opacity,
            "angle": self.angle, "speed": self.speed, "start": self.start, "end": self.end,
            "spacing": self.spacing, "path_points": [list(p) for p in self.path_points],
            "font": self.font,
        }

    @classmethod
    def from_dict(cls, data: object) -> WatermarkSpec:
        if not isinstance(data, dict):
            return cls()
        points = []
        for item in data.get("path_points") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                points.append((float(item[0]), float(item[1])))
        base = cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            style=str(data.get("style", base.style)),
            text=str(data.get("text", "")),
            image=str(data.get("image", "")),
            x=float(data.get("x", base.x)),
            y=float(data.get("y", base.y)),
            size=int(data.get("size", base.size)),
            opacity=float(data.get("opacity", base.opacity)),
            angle=float(data.get("angle", base.angle)),
            speed=float(data.get("speed", base.speed)),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            spacing=int(data.get("spacing", base.spacing)),
            path_points=tuple(points),
            font=str(data.get("font", "")),
        )


@dataclass(frozen=True)
class MaskSpec:
    """Region in relative coordinates (0–1) so it can move across resolutions."""

    mode: str = "blur"
    x: float = 0.0
    y: float = 0.78
    w: float = 1.0
    h: float = 0.16
    src_x: float = 0.0
    src_y: float = 0.60
    src_w: float = 1.0
    src_h: float = 0.16
    feather: int = 10
    opacity: float = 1.0
    blur: float = 12.0
    start: float = 0.0
    end: float = 0.0

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "src_x": self.src_x, "src_y": self.src_y, "src_w": self.src_w, "src_h": self.src_h,
            "feather": self.feather, "opacity": self.opacity, "blur": self.blur,
            "start": self.start, "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: object) -> MaskSpec:
        if not isinstance(data, dict):
            return cls()
        base = cls()
        fields = {}
        for key in base.to_dict():
            if key not in data:
                fields[key] = getattr(base, key)
            elif key in {"feather"}:
                fields[key] = int(data[key])
            elif key == "mode":
                fields[key] = str(data[key])
            else:
                fields[key] = float(data[key])
        return cls(**fields)


PIP_POSITIONS = ("tl", "tr", "bl", "br", "center")


@dataclass(frozen=True)
class PipSpec:
    """A real video laid over the main picture. Disabled or empty means it is not applied."""

    enabled: bool = False
    path: str = ""
    position: str = "tr"
    size: float = 0.22
    opacity: float = 0.08
    opacity_jitter: float = 0.03
    position_jitter: float = 0.02
    size_jitter: float = 0.0
    audio_percent: float = 0.0
    short_mode: str = "loop"
    start: float = 0.0
    end: float = 0.0

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled, "path": self.path, "position": self.position, "size": self.size,
            "opacity": self.opacity, "opacity_jitter": self.opacity_jitter,
            "position_jitter": self.position_jitter, "size_jitter": self.size_jitter,
            "audio_percent": self.audio_percent, "short_mode": self.short_mode,
            "start": self.start, "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: object) -> PipSpec:
        if not isinstance(data, dict):
            return cls()
        base = cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            path=str(data.get("path", "")),
            position=str(data.get("position", base.position)),
            size=float(data.get("size", base.size)),
            opacity=float(data.get("opacity", base.opacity)),
            opacity_jitter=float(data.get("opacity_jitter", base.opacity_jitter)),
            position_jitter=float(data.get("position_jitter", base.position_jitter)),
            size_jitter=float(data.get("size_jitter", base.size_jitter)),
            audio_percent=float(data.get("audio_percent", base.audio_percent)),
            short_mode=str(data.get("short_mode", base.short_mode)),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
        )


@dataclass(frozen=True)
class PhotoInsertSpec:
    """Real photos inserted into the timeline. Disabled or empty means they are not applied."""

    enabled: bool = False
    paths: tuple[str, ...] = ()
    interval: float = 3.0
    interval_jitter: float = 0.4
    hold_ms: int = 400
    hold_jitter_ms: int = 80
    fade_ms: int = 120

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled, "paths": list(self.paths), "interval": self.interval,
            "interval_jitter": self.interval_jitter, "hold_ms": self.hold_ms,
            "hold_jitter_ms": self.hold_jitter_ms, "fade_ms": self.fade_ms,
        }

    @classmethod
    def from_dict(cls, data: object) -> PhotoInsertSpec:
        if not isinstance(data, dict):
            return cls()
        base = cls()
        paths = tuple(str(item) for item in (data.get("paths") or []) if str(item).strip())
        return cls(
            enabled=bool(data.get("enabled", False)),
            paths=paths,
            interval=float(data.get("interval", base.interval)),
            interval_jitter=float(data.get("interval_jitter", base.interval_jitter)),
            hold_ms=int(data.get("hold_ms", base.hold_ms)),
            hold_jitter_ms=int(data.get("hold_jitter_ms", base.hold_jitter_ms)),
            fade_ms=int(data.get("fade_ms", base.fade_ms)),
        )


@dataclass(frozen=True)
class ProcessOptions:
    """Explicit processing switches. Anything left disabled is not applied."""

    scale: RangeSpec = field(default_factory=lambda: RangeSpec(False, 1.0, 1.0))
    crop_enabled: bool = False
    crop_x: int = 0
    crop_y: int = 0
    crop_w: int = 0
    crop_h: int = 0
    mirror_h: bool = False
    mirror_v: bool = False
    rotate: RangeSpec = field(default_factory=lambda: RangeSpec(False, 0.0, 0.0))
    brightness: RangeSpec = field(default_factory=lambda: RangeSpec(False, 0.0, 0.0))
    contrast: RangeSpec = field(default_factory=lambda: RangeSpec(False, 1.0, 1.0))
    saturation: RangeSpec = field(default_factory=lambda: RangeSpec(False, 1.0, 1.0))
    color_temp: RangeSpec = field(default_factory=lambda: RangeSpec(False, 0.0, 0.0))
    sharpen: RangeSpec = field(default_factory=lambda: RangeSpec(False, 0.0, 0.0))
    blur: RangeSpec = field(default_factory=lambda: RangeSpec(False, 0.0, 0.0))
    speed: RangeSpec = field(default_factory=lambda: RangeSpec(False, 1.0, 1.0))
    drop_enabled: bool = False
    drop_interval: int = 8
    fps: int = 0
    codec: str = "libx264"
    crf: int = 19
    encoder_preset: str = "medium"
    volume_db: RangeSpec = field(default_factory=lambda: RangeSpec(False, 0.0, 0.0))
    denoise: bool = False
    denoise_nr: float = 12.0
    eq_enabled: bool = False
    eq_low: float = 0.0
    eq_mid: float = 0.0
    eq_high: float = 0.0
    loudnorm: bool = False
    mute: bool = False
    bgm_mode: str = "off"
    bgm_path: str = ""
    bgm_volume: float = 0.40
    watermark: WatermarkSpec = field(default_factory=WatermarkSpec)
    masks: tuple[MaskSpec, ...] = ()
    pip: PipSpec = field(default_factory=PipSpec)
    photos: PhotoInsertSpec = field(default_factory=PhotoInsertSpec)
    font: str = ""

    def to_dict(self) -> dict:
        return {
            "scale": self.scale.to_dict(), "crop_enabled": self.crop_enabled,
            "crop_x": self.crop_x, "crop_y": self.crop_y, "crop_w": self.crop_w, "crop_h": self.crop_h,
            "mirror_h": self.mirror_h, "mirror_v": self.mirror_v, "rotate": self.rotate.to_dict(),
            "brightness": self.brightness.to_dict(), "contrast": self.contrast.to_dict(),
            "saturation": self.saturation.to_dict(), "color_temp": self.color_temp.to_dict(),
            "sharpen": self.sharpen.to_dict(), "blur": self.blur.to_dict(), "speed": self.speed.to_dict(),
            "drop_enabled": self.drop_enabled, "drop_interval": self.drop_interval, "fps": self.fps,
            "codec": self.codec, "crf": self.crf, "encoder_preset": self.encoder_preset,
            "volume_db": self.volume_db.to_dict(), "denoise": self.denoise, "denoise_nr": self.denoise_nr,
            "eq_enabled": self.eq_enabled, "eq_low": self.eq_low, "eq_mid": self.eq_mid, "eq_high": self.eq_high,
            "loudnorm": self.loudnorm, "mute": self.mute, "bgm_mode": self.bgm_mode,
            "bgm_path": self.bgm_path, "bgm_volume": self.bgm_volume,
            "watermark": self.watermark.to_dict(), "masks": [m.to_dict() for m in self.masks],
            "pip": self.pip.to_dict(), "photos": self.photos.to_dict(),
            "font": self.font,
        }

    @classmethod
    def from_dict(cls, data: object) -> ProcessOptions:
        if not isinstance(data, dict):
            raise ValueError("处理方案必须是 JSON 对象")
        base = cls()
        masks = tuple(MaskSpec.from_dict(item) for item in data.get("masks") or [])
        return cls(
            scale=RangeSpec.from_dict(data.get("scale"), base.scale),
            crop_enabled=bool(data.get("crop_enabled", False)),
            crop_x=int(data.get("crop_x", 0)), crop_y=int(data.get("crop_y", 0)),
            crop_w=int(data.get("crop_w", 0)), crop_h=int(data.get("crop_h", 0)),
            mirror_h=bool(data.get("mirror_h", False)), mirror_v=bool(data.get("mirror_v", False)),
            rotate=RangeSpec.from_dict(data.get("rotate"), base.rotate),
            brightness=RangeSpec.from_dict(data.get("brightness"), base.brightness),
            contrast=RangeSpec.from_dict(data.get("contrast"), base.contrast),
            saturation=RangeSpec.from_dict(data.get("saturation"), base.saturation),
            color_temp=RangeSpec.from_dict(data.get("color_temp"), base.color_temp),
            sharpen=RangeSpec.from_dict(data.get("sharpen"), base.sharpen),
            blur=RangeSpec.from_dict(data.get("blur"), base.blur),
            speed=RangeSpec.from_dict(data.get("speed"), base.speed),
            drop_enabled=bool(data.get("drop_enabled", False)),
            drop_interval=int(data.get("drop_interval", base.drop_interval)),
            fps=int(data.get("fps", 0)),
            codec=str(data.get("codec", base.codec)),
            crf=int(data.get("crf", base.crf)),
            encoder_preset=str(data.get("encoder_preset", base.encoder_preset)),
            volume_db=RangeSpec.from_dict(data.get("volume_db"), base.volume_db),
            denoise=bool(data.get("denoise", False)),
            denoise_nr=float(data.get("denoise_nr", base.denoise_nr)),
            eq_enabled=bool(data.get("eq_enabled", False)),
            eq_low=float(data.get("eq_low", 0.0)), eq_mid=float(data.get("eq_mid", 0.0)),
            eq_high=float(data.get("eq_high", 0.0)),
            loudnorm=bool(data.get("loudnorm", False)), mute=bool(data.get("mute", False)),
            bgm_mode=str(data.get("bgm_mode", "off")), bgm_path=str(data.get("bgm_path", "")),
            bgm_volume=float(data.get("bgm_volume", base.bgm_volume)),
            watermark=WatermarkSpec.from_dict(data.get("watermark")),
            masks=masks,
            pip=PipSpec.from_dict(data.get("pip")),
            photos=PhotoInsertSpec.from_dict(data.get("photos")),
            font=str(data.get("font", "")),
        )


@dataclass(frozen=True)
class JobSpec:
    source: Path
    output_dir: Path
    version: int
    seed: int
    preset: Preset
    verify_full: bool = True
    job_id: str = field(default_factory=lambda: uuid4().hex)
    options: ProcessOptions | None = None

    def derived_seed(self) -> int:
        content = f"{self.seed}\0{self.source.resolve()}\0{self.version}".encode("utf-8")
        return int.from_bytes(hashlib.sha256(content).digest()[:8], "big")


def make_jobs(sources: list[Path], output_dir: Path, versions: int,
              seed: int, preset: Preset, verify_full: bool = True,
              options: ProcessOptions | None = None) -> list[JobSpec]:
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
                    version=v, seed=seed, preset=preset, verify_full=verify_full,
                    options=options)
            for s in sources for v in range(1, versions + 1)]
