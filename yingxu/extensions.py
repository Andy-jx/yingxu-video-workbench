"""Watermark and subtitle-mask helpers backed by the real filter builder.

Calling either class without a configuration still raises NotImplementedError.
Passing a spec returns the FFmpeg graph that the encoder actually runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import MaskSpec, ProcessOptions, WatermarkSpec
from .processing import build_plan, sample_options


@dataclass(frozen=True)
class FilterContext:
    width: int
    height: int
    duration_seconds: float


class DynamicWatermarkExtension:
    def __init__(self, watermark: WatermarkSpec | None = None, font: str = "") -> None:
        self.watermark = watermark
        self.font = font

    def build_filters(self, context: FilterContext) -> list[str]:
        if self.watermark is None:
            raise NotImplementedError("未配置水印参数")
        if not self.watermark.enabled:
            return []
        options = ProcessOptions(watermark=self.watermark, font=self.font)
        return [_graph(options, context)]


class SubtitleMaskExtension:
    def __init__(self, masks: tuple[MaskSpec, ...] | None = None) -> None:
        self.masks = masks

    def build_filters(self, context: FilterContext) -> list[str]:
        if self.masks is None:
            raise NotImplementedError("未配置字幕蒙版")
        if not self.masks:
            return []
        return [_graph(ProcessOptions(masks=self.masks), context)]


def _graph(options: ProcessOptions, context: FilterContext) -> str:
    media = {
        "width": context.width,
        "height": context.height,
        "duration": context.duration_seconds,
        "fps": 25.0,
        "video": {"index": 0},
        "audio_count": 0,
    }
    plan = build_plan(options, sample_options(options, 1), media, include_audio=False)
    if not plan.graph:
        raise ValueError("当前参数没有生成滤镜")
    return plan.graph
