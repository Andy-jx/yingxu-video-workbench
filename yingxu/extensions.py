"""Explicit extension points; not implemented in V0."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FilterContext:
    width: int
    height: int
    duration_seconds: float


class DynamicWatermarkExtension:
    def build_filters(self, context: FilterContext) -> list[str]:
        raise NotImplementedError("V0 尚未实现动态水印")


class SubtitleMaskExtension:
    def build_filters(self, context: FilterContext) -> list[str]:
        raise NotImplementedError("V0 尚未实现字幕遮罩")
