"""Turn ProcessOptions into a real FFmpeg filter graph. Disabled features are omitted."""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from .engine import MediaError, _flags
from .models import (
    ENCODER_PRESETS, MASK_MODES, VIDEO_CODECS, WATERMARK_STYLES,
    MaskSpec, Preset, ProcessOptions, RangeSpec, WatermarkSpec,
)

RANGE_ORDER = (
    "scale", "rotate", "brightness", "contrast", "saturation",
    "color_temp", "sharpen", "blur", "speed", "volume_db",
)


@dataclass
class FilterPlan:
    graph: str = ""
    extra_inputs: list[tuple[str, str]] = field(default_factory=list)
    vout: str = ""
    aout: str = ""
    map_source_audio: bool = False
    has_audio: bool = False
    parameters: dict = field(default_factory=dict)
    expected_duration: float = 0.0
    force_verify: bool = False


def options_from_preset(preset: Preset) -> ProcessOptions:
    """Map a V0 color preset onto switches. Identity ranges stay off."""

    def as_range(pair: tuple[float, float], identity: float) -> RangeSpec:
        enabled = abs(pair[0] - identity) > 1e-8 or abs(pair[1] - identity) > 1e-8
        return RangeSpec(enabled, pair[0], pair[1])

    return ProcessOptions(
        brightness=as_range(preset.brightness, 0.0),
        contrast=as_range(preset.contrast, 1.0),
        saturation=as_range(preset.saturation, 1.0),
        volume_db=as_range(preset.audio_gain_db, 0.0),
        crf=preset.crf,
        encoder_preset=preset.encoder_preset,
    )


def load_profile(path: str | Path) -> ProcessOptions:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("options"), dict):
        data = data["options"]
    return ProcessOptions.from_dict(data)


def sample_options(options: ProcessOptions, seed: int) -> dict[str, float | None]:
    """Draw every range so turning one switch on does not reshuffle the others."""
    rng = __import__("random").Random(seed)
    drawn: dict[str, float | None] = {}
    for name in RANGE_ORDER:
        spec: RangeSpec = getattr(options, name)
        value = round(spec.sample(rng), 5)
        drawn[name] = value if spec.enabled else None
    return drawn


def validate_options(options: ProcessOptions) -> None:
    _range(options.scale, "缩放", 0.5, 2.0)
    _range(options.rotate, "旋转", -180.0, 180.0)
    _range(options.brightness, "亮度", -0.25, 0.25)
    _range(options.contrast, "对比度", 0.5, 2.0)
    _range(options.saturation, "饱和度", 0.0, 3.0)
    _range(options.color_temp, "色温", -1.0, 1.0)
    _range(options.sharpen, "锐化", 0.0, 2.0)
    _range(options.blur, "模糊", 0.0, 30.0)
    _range(options.speed, "变速", 0.25, 4.0)
    _range(options.volume_db, "音量", -18.0, 18.0)
    if options.drop_enabled and not 2 <= options.drop_interval <= 30:
        raise ValueError("抽帧间隔必须在 2～30 帧之间")
    if options.fps not in {0, 24, 25, 30, 50, 60}:
        raise ValueError("输出帧率只支持保持原帧率，或 24/25/30/50/60")
    if options.codec not in VIDEO_CODECS:
        raise ValueError("编码器无效")
    if options.encoder_preset not in ENCODER_PRESETS:
        raise ValueError("编码速度预设无效")
    if not 14 <= options.crf <= 30:
        raise ValueError("CRF 必须在 14～30 之间")
    if options.bgm_mode not in {"off", "replace", "mix"}:
        raise ValueError("背景音乐模式无效")
    if options.bgm_mode != "off":
        if not options.bgm_path or not Path(options.bgm_path).is_file():
            raise ValueError("请选择存在的背景音乐文件")
        if not 0 <= options.bgm_volume <= 2:
            raise ValueError("背景音乐音量必须在 0～2 之间")
    if not 0 <= options.denoise_nr <= 40:
        raise ValueError("降噪强度必须在 0～40 之间")
    for name, value in (("低频", options.eq_low), ("中频", options.eq_mid), ("高频", options.eq_high)):
        if not -12 <= value <= 12:
            raise ValueError(f"均衡{name}必须在 -12～12 dB 之间")
    _validate_watermark(options.watermark)
    for index, mask in enumerate(options.masks, start=1):
        _validate_mask(mask, index)
    if options.font and not Path(options.font).is_file():
        raise ValueError(f"找不到字体文件: {options.font}")


def validate_against_media(options: ProcessOptions, width: int, height: int) -> None:
    if not options.crop_enabled:
        return
    if options.crop_w < 16 or options.crop_h < 16:
        raise ValueError("裁剪宽高至少为 16 像素")
    if options.crop_x < 0 or options.crop_y < 0:
        raise ValueError("裁剪位置不能为负")
    if options.crop_x + options.crop_w > width or options.crop_y + options.crop_h > height:
        raise ValueError(f"裁剪区域超出画面（{width}×{height}）")


def enabled_summary(options: ProcessOptions) -> list[str]:
    lines: list[str] = []
    if options.scale.enabled:
        lines.append(f"缩放 {options.scale.low:.3f}～{options.scale.high:.3f}")
    if options.crop_enabled:
        lines.append(f"裁剪 {options.crop_w}×{options.crop_h}")
    if options.mirror_h:
        lines.append("水平镜像")
    if options.mirror_v:
        lines.append("垂直镜像")
    if options.rotate.enabled:
        lines.append(f"旋转 {options.rotate.low:.1f}°～{options.rotate.high:.1f}°")
    if options.brightness.enabled:
        lines.append(f"亮度 {options.brightness.low:+.3f}～{options.brightness.high:+.3f}")
    if options.contrast.enabled:
        lines.append(f"对比度 {options.contrast.low:.3f}～{options.contrast.high:.3f}")
    if options.saturation.enabled:
        lines.append(f"饱和度 {options.saturation.low:.3f}～{options.saturation.high:.3f}")
    if options.color_temp.enabled:
        lines.append(f"色温 {options.color_temp.low:+.2f}～{options.color_temp.high:+.2f}")
    if options.sharpen.enabled:
        lines.append(f"锐化 {options.sharpen.low:.2f}～{options.sharpen.high:.2f}")
    if options.blur.enabled:
        lines.append(f"模糊 {options.blur.low:.2f}～{options.blur.high:.2f}")
    if options.speed.enabled:
        lines.append(f"变速 {options.speed.low:.3f}～{options.speed.high:.3f}")
    if options.drop_enabled:
        lines.append(f"抽帧 每{options.drop_interval}帧删1帧")
    if options.fps:
        lines.append(f"帧率 {options.fps}fps")
    if options.volume_db.enabled:
        lines.append(f"音量 {options.volume_db.low:+.2f}～{options.volume_db.high:+.2f}dB")
    if options.denoise:
        lines.append("降噪")
    if options.eq_enabled:
        lines.append("三段均衡")
    if options.loudnorm:
        lines.append("响度标准化")
    if options.mute:
        lines.append("原声静音")
    if options.bgm_mode == "replace":
        lines.append("背景音乐替换")
    elif options.bgm_mode == "mix":
        lines.append("背景音乐混合")
    if options.watermark.enabled:
        lines.append("水印·" + _style_name(options.watermark.style))
    if options.masks:
        lines.append(f"字幕蒙版 {len(options.masks)} 段")
    lines.append(f"编码 {_codec_name(options.codec)} CRF{options.crf}")
    return lines


def risky_warnings(options: ProcessOptions) -> list[str]:
    lines: list[str] = []
    if options.mirror_h or options.mirror_v:
        lines.append("镜像会翻转画面。镜像默认关闭，只有勾选后才会执行。")
    if options.rotate.enabled:
        lines.append("旋转可能露出黑边，或改变画面方向。")
    if options.crop_enabled:
        lines.append("裁剪会切掉区域外的画面，且不会自动补回。")
    if options.scale.enabled and min(options.scale.low, options.scale.high) < 0.999:
        lines.append("缩小后会用黑边补回原尺寸。")
    if options.drop_enabled:
        lines.append(f"抽帧会每 {options.drop_interval} 帧删除 1 帧，成片变短，音频按同一比例加速。")
    if options.speed.enabled and (abs(options.speed.low - 1) > 1e-3 or abs(options.speed.high - 1) > 1e-3):
        lines.append("变速会改变时长。音频用 atempo 跟着变速，尽量保持音调。")
    if options.mute:
        lines.append("原声会被静音。")
    if options.bgm_mode == "replace":
        lines.append("原声会被背景音乐替换。")
    if options.masks:
        lines.append("字幕蒙版是区域覆盖，不是无痕修复。人物、商品或复杂背景上可能不自然。")
    return lines


def build_plan(options: ProcessOptions, sampled: dict[str, float | None], media: dict, *,
               preview_time: float | None = None, time_origin: float = 0.0,
               include_timing: bool = True, include_audio: bool = True) -> FilterPlan:
    validate_options(options)
    width = int(media["width"])
    height = int(media["height"])
    duration = float(media["duration"])
    validate_against_media(options, width, height)
    speed = sampled.get("speed") or 1.0
    drop_ratio = (options.drop_interval / (options.drop_interval - 1)) if options.drop_enabled else 1.0
    tempo = speed * drop_ratio
    if abs(tempo) < 1e-6:
        raise ValueError("变速倍率无效")
    expected = duration / tempo
    out_w, out_h = _output_size(width, height, options, sampled)
    font = _resolve_font(options)
    video_filters = _linear_video_filters(options, sampled, width, height, include_timing=include_timing and preview_time is None)
    mask_filters, out_w, out_h = _active_masks(options, preview_time, out_w, out_h)
    wm_filters, wm_inputs, wm_label = _watermark_filters(
        options.watermark, out_w, out_h, expected, font, preview_time, time_origin,
    )
    audio_count = int(media.get("audio_count") or 0)
    audio_filters = []
    use_bgm = False
    if include_audio:
        audio_filters = _linear_audio_filters(options, sampled, tempo if include_timing else 1.0, audio_count)
        use_bgm = options.bgm_mode != "off"
    needs_graph = bool(video_filters or mask_filters or wm_filters or audio_filters or use_bgm)
    plan = FilterPlan(expected_duration=expected, force_verify=_needs_forced_verify(options, sampled))
    plan.parameters = _parameters(options, sampled, tempo, expected, font)
    if not needs_graph:
        plan.map_source_audio = audio_count > 0 and include_audio
        plan.has_audio = plan.map_source_audio
        return plan

    segments: list[str] = []
    counter = 0
    current = f"0:{int(media['video']['index'])}"

    def chain(body: str) -> None:
        nonlocal counter, current
        counter += 1
        out = f"v{counter}"
        segments.append(f"[{current}]{body}[{out}]")
        current = out

    if video_filters:
        chain(",".join(video_filters))
    for body in mask_filters:
        rendered = body.replace("__SRC__", current)
        segments.append(rendered)
        current = rendered.rsplit("[", 1)[-1].rstrip("]")
    if wm_filters:
        segments.extend(part.replace("__SRC__", current) for part in wm_filters)
        if wm_label:
            current = wm_label
    chain("format=yuv420p")
    plan.vout = current
    plan.extra_inputs.extend(wm_inputs)

    bgm_mode = "replace" if options.mute and options.bgm_mode == "mix" else options.bgm_mode
    if bgm_mode != "off":
        plan.extra_inputs.append(("bgm", options.bgm_path))
        bgm_index = 1 + sum(1 for kind, _ in plan.extra_inputs if kind == "image")
        bgm = (f"[{bgm_index}:a]atrim=0:{max(0.1, expected):.3f},asetpts=PTS-STARTPTS,"
               f"volume={options.bgm_volume:.4f}")
        if bgm_mode == "mix" and audio_count:
            base = ",".join(audio_filters) if audio_filters else "anull"
            segments.append(f"[0:a:0]{base}[abase]")
            segments.append(f"{bgm}[bgma]")
            segments.append("[abase][bgma]amix=inputs=2:duration=first:normalize=0[aout]")
            plan.aout = "aout"
        else:
            segments.append(f"{bgm}[aout]")
            plan.aout = "aout"
        plan.has_audio = True
    elif audio_filters and audio_count:
        segments.append(f"[0:a:0]{','.join(audio_filters)}[aout]")
        plan.aout = "aout"
        plan.has_audio = True
    elif audio_count:
        plan.map_source_audio = True
        plan.has_audio = True
    if audio_count > 1 and (audio_filters or bgm_mode != "off"):
        plan.parameters["audio_note"] = "只处理第一条音轨"
    plan.graph = ";".join(segments)
    return plan


def command_from_plan(ffmpeg: str, source: str, output: str, plan: FilterPlan,
                      media: dict, options: ProcessOptions, *,
                      still_time: float | None = None,
                      clip: tuple[float, float] | None = None) -> list[str]:
    preview = still_time is not None or clip is not None
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y" if preview else "-n"]
    if clip is not None:
        cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1]:.3f}"]
    elif still_time is not None:
        cmd += ["-ss", f"{still_time:.3f}"]
    cmd += ["-i", source]
    for kind, path in plan.extra_inputs:
        if kind == "image":
            cmd += ["-framerate", "25", "-loop", "1", "-i", path]
        else:
            cmd += ["-stream_loop", "-1", "-i", path]
    if plan.graph:
        cmd += ["-filter_complex", plan.graph, "-map", f"[{plan.vout}]"]
        if still_time is None and plan.aout:
            cmd += ["-map", f"[{plan.aout}]"]
        elif still_time is None and plan.map_source_audio:
            cmd += ["-map", "0:a?"]
    else:
        cmd += ["-map", f"0:{int(media['video']['index'])}"]
        if still_time is None and plan.map_source_audio:
            cmd += ["-map", "0:a?"]
    if still_time is not None:
        cmd += ["-frames:v", "1", "-an", output]
        return cmd
    if clip is not None:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p"]
    else:
        cmd += _codec_args(options)
    if plan.has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k" if clip else "192k"]
    if clip is None:
        cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-nostats"]
    cmd += ["-f", "mp4", output]
    return cmd


def prepare_encode(job, src: dict, ffmpeg: str, temp: Path) -> tuple[list[str], dict, float, bool]:
    options = job.options
    if not isinstance(options, ProcessOptions):
        raise TypeError("任务缺少处理方案")
    sampled = sample_options(options, job.derived_seed())
    plan = build_plan(options, sampled, src)
    command = command_from_plan(ffmpeg, str(job.source), str(temp), plan, src, options)
    return command, plan.parameters, plan.expected_duration, plan.force_verify


def render_still(ffmpeg: str, source: Path, media: dict, options: ProcessOptions,
                 time_sec: float, output: Path, seed: int) -> None:
    sampled = sample_options(options, seed)
    plan = build_plan(options, sampled, media, preview_time=time_sec, include_timing=False, include_audio=False)
    command = command_from_plan(ffmpeg, str(source), str(output), plan, media, options, still_time=time_sec)
    _run(command, "预览帧失败")


def render_clip(ffmpeg: str, source: Path, media: dict, options: ProcessOptions,
                start: float, length: float, output: Path, seed: int) -> None:
    sampled = sample_options(options, seed)
    plan = build_plan(options, sampled, media, time_origin=start, include_timing=True)
    command = command_from_plan(
        ffmpeg, str(source), str(output), plan, media, options, clip=(start, length),
    )
    _run(command, "效果片段失败")


def extract_frame(ffmpeg: str, source: Path, time_sec: float, output: Path) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, time_sec):.3f}",
               "-i", str(source), "-frames:v", "1", "-an", str(output)]
    _run(command, "读取画面失败")


def list_video_encoders(ffmpeg: str) -> set[str]:
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True,
                              text=True, timeout=20, creationflags=_flags())
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found = set()
    text = proc.stdout or ""
    for name in VIDEO_CODECS:
        if re.search(rf"\b{name}\b", text):
            found.add(name)
    return found


def find_cjk_font() -> str | None:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\msjh.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    fc = shutil.which("fc-list")
    if not fc:
        return None
    try:
        out = subprocess.check_output([fc, ":lang=zh", "file"], text=True, timeout=5, creationflags=_flags())
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return None
    for line in out.splitlines():
        font = line.split(":", 1)[0].strip()
        if font and Path(font).is_file():
            return font
    return None


def frame_luma(ffmpeg: str, path: Path, time_sec: float) -> float | None:
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, time_sec):.3f}",
             "-i", str(path), "-frames:v", "1", "-vf", "signalstats,metadata=mode=print:file=-",
             "-f", "null", "-"],
            capture_output=True, timeout=60, creationflags=_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or b"").decode("utf-8", "replace")
    match = re.search(r"signalstats\.YAVG=([0-9.]+)", text)
    return float(match.group(1)) if match else None


def widget_rect_to_relative(widget_w: float, widget_h: float, frame_w: float, frame_h: float,
                            x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float] | None:
    """Convert a drag rectangle on a letterboxed preview into 0–1 video coordinates."""
    if min(widget_w, widget_h, frame_w, frame_h) < 2:
        return None
    scale = min(widget_w / frame_w, widget_h / frame_h)
    disp_w = frame_w * scale
    disp_h = frame_h * scale
    off_x = (widget_w - disp_w) / 2
    off_y = (widget_h - disp_h) / 2

    def convert(x: float, y: float) -> tuple[float, float]:
        return (x - off_x) / disp_w, (y - off_y) / disp_h

    left, top = convert(min(x0, x1), min(y0, y1))
    right, bottom = convert(max(x0, x1), max(y0, y1))
    left, top = max(0.0, left), max(0.0, top)
    right, bottom = min(1.0, right), min(1.0, bottom)
    if right - left < 0.01 or bottom - top < 0.01:
        return None
    return left, top, right - left, bottom - top


def path_position(points: tuple[tuple[float, float], ...], time_sec: float, loop: float,
                  width: int, height: int) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    if len(points) == 1 or loop <= 0:
        return points[0][0] * width, points[0][1] * height
    segs = len(points) - 1
    tri = 1 - abs(2 * ((time_sec % loop) / loop) - 1)
    position = tri * segs
    index = min(int(position), segs - 1)
    blend = position - index
    x = points[index][0] + (points[index + 1][0] - points[index][0]) * blend
    y = points[index][1] + (points[index + 1][1] - points[index][1]) * blend
    return x * width, y * height


def _range(spec: RangeSpec, label: str, low: float, high: float) -> None:
    if not spec.enabled:
        return
    if not (low <= spec.low <= high and low <= spec.high <= high):
        raise ValueError(f"{label}超出允许范围 {low:g}～{high:g}")


def _validate_watermark(mark: WatermarkSpec) -> None:
    if not mark.enabled:
        return
    if mark.style not in WATERMARK_STYLES:
        raise ValueError("水印样式无效")
    if not 0.0 <= mark.opacity <= 1.0:
        raise ValueError("水印透明度必须在 0～1 之间")
    if not 8 <= mark.size <= 256:
        raise ValueError("水印大小必须在 8～256 之间")
    if not 0.0 <= mark.x <= 1.0 or not 0.0 <= mark.y <= 1.0:
        raise ValueError("水印位置必须在画面比例 0～1 之间")
    if not -180 <= mark.angle <= 180:
        raise ValueError("水印角度必须在 -180～180 之间")
    if not 0 <= mark.speed <= 2000:
        raise ValueError("水印速度必须在 0～2000 像素/秒之间")
    if not 40 <= mark.spacing <= 2000:
        raise ValueError("水印间距必须在 40～2000 之间")
    if mark.start < 0 or mark.end < 0 or (mark.end > 0 and mark.end <= mark.start):
        raise ValueError("水印出现时间无效")
    kind = _mark_kind(mark)
    if kind == "text" and not mark.text.strip():
        raise ValueError("请填写水印文字")
    if kind == "image" and (not mark.image or not Path(mark.image).is_file()):
        raise ValueError("请选择存在的水印图片")
    if mark.style == "path" and len(mark.path_points) < 2:
        raise ValueError("自定义轨迹至少需要两个坐标点，格式如 0.1,0.2;0.8,0.3")
    for x, y in mark.path_points:
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError("轨迹坐标必须在 0～1 之间")
    if mark.font and not Path(mark.font).is_file():
        raise ValueError(f"找不到水印字体: {mark.font}")


def _validate_mask(mask: MaskSpec, index: int) -> None:
    if mask.mode not in MASK_MODES:
        raise ValueError(f"第 {index} 段蒙版方式无效")
    for label, value in (("X", mask.x), ("Y", mask.y), ("源X", mask.src_x), ("源Y", mask.src_y)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"第 {index} 段蒙版{label}必须在 0～1 之间")
    for label, value in (("宽度", mask.w), ("高度", mask.h), ("源宽度", mask.src_w), ("源高度", mask.src_h)):
        if not 0.01 <= value <= 1.0:
            raise ValueError(f"第 {index} 段蒙版{label}无效")
    if mask.x + mask.w > 1.001 or mask.y + mask.h > 1.001:
        raise ValueError(f"第 {index} 段蒙版超出画面")
    if mask.src_x + mask.src_w > 1.001 or mask.src_y + mask.src_h > 1.001:
        raise ValueError(f"第 {index} 段蒙版的复制源超出画面")
    if not 0 <= mask.feather <= 80:
        raise ValueError(f"第 {index} 段羽化必须在 0～80 之间")
    if not 0.0 <= mask.opacity <= 1.0:
        raise ValueError(f"第 {index} 段蒙版透明度无效")
    if mask.mode == "blur" and not 0.2 <= mask.blur <= 40:
        raise ValueError(f"第 {index} 段模糊强度必须在 0.2～40 之间")
    if mask.start < 0 or (mask.end > 0 and mask.end <= mask.start):
        raise ValueError(f"第 {index} 段蒙版时间无效")


def _parameters(options: ProcessOptions, sampled: dict, tempo: float, expected: float, font: str | None) -> dict:
    values = {key: value for key, value in sampled.items() if value is not None}
    params: dict = {
        "values": values,
        "tempo": round(tempo, 5),
        "expected_duration": round(expected, 3),
        "codec": options.codec,
        "crf": options.crf,
        "enabled": enabled_summary(options),
    }
    if options.crop_enabled:
        params["crop"] = [options.crop_x, options.crop_y, options.crop_w, options.crop_h]
    if options.mirror_h:
        params["mirror_h"] = True
    if options.mirror_v:
        params["mirror_v"] = True
    if options.drop_enabled:
        params["drop_interval"] = options.drop_interval
    if options.fps:
        params["fps"] = options.fps
    if options.watermark.enabled:
        params["watermark"] = {
            "style": options.watermark.style, "text": options.watermark.text,
            "image": options.watermark.image, "opacity": options.watermark.opacity,
            "x": options.watermark.x, "y": options.watermark.y, "size": options.watermark.size,
            "angle": options.watermark.angle, "speed": options.watermark.speed,
            "start": options.watermark.start, "end": options.watermark.end,
        }
        if font:
            params["font"] = font
    if options.masks:
        params["masks"] = [mask.to_dict() for mask in options.masks]
    if options.mute:
        params["mute"] = True
    if options.bgm_mode != "off":
        params["bgm"] = {"mode": "replace" if options.mute and options.bgm_mode == "mix" else options.bgm_mode,
                         "path": options.bgm_path, "volume": options.bgm_volume}
    if options.eq_enabled:
        params["eq"] = [options.eq_low, options.eq_mid, options.eq_high]
    if options.denoise:
        params["denoise_nr"] = options.denoise_nr
    if options.loudnorm:
        params["loudnorm"] = True
    params["forced_full_verify"] = _needs_forced_verify(options, sampled)
    return params


def _needs_forced_verify(options: ProcessOptions, sampled: dict) -> bool:
    speed = sampled.get("speed")
    return options.drop_enabled or (speed is not None and abs(speed - 1) > 1e-3)


def _output_size(width: int, height: int, options: ProcessOptions, sampled: dict) -> tuple[int, int]:
    out_w, out_h = width, height
    if options.crop_enabled:
        out_w, out_h = _even(options.crop_w), _even(options.crop_h)
    angle = sampled.get("rotate")
    if options.rotate.enabled and angle is not None:
        folded = abs(angle) % 360
        if abs(folded - 90) < 0.05 or abs(folded - 270) < 0.05:
            out_w, out_h = out_h, out_w
    return out_w, out_h


def _linear_video_filters(options: ProcessOptions, sampled: dict, width: int, height: int,
                          include_timing: bool) -> list[str]:
    filters: list[str] = []
    work_w, work_h = width, height
    if options.crop_enabled:
        cw, ch = _even(options.crop_w), _even(options.crop_h)
        cx, cy = _even_floor(options.crop_x), _even_floor(options.crop_y)
        filters.append(f"crop={cw}:{ch}:{cx}:{cy}")
        work_w, work_h = cw, ch
    scale = sampled.get("scale")
    if scale is not None and abs(scale - 1) >= 0.001:
        filters.extend(_zoom(scale, work_w, work_h))
    if options.mirror_h:
        filters.append("hflip")
    if options.mirror_v:
        filters.append("vflip")
    angle = sampled.get("rotate") if options.rotate.enabled else None
    if angle is not None and abs(angle) >= 0.05:
        filters.extend(_rotate(angle))
    eq = _eq_filter(sampled)
    if eq:
        filters.append(eq)
    temp = sampled.get("color_temp")
    if temp is not None and abs(temp) >= 0.001:
        kelvin = max(1000.0, min(40000.0, 6500.0 - temp * 2500.0))
        filters.append(f"colortemperature=temperature={kelvin:.1f}")
    sharp = sampled.get("sharpen")
    if sharp is not None and sharp >= 0.01:
        filters.append(f"unsharp=5:5:{sharp:.4f}")
    blur = sampled.get("blur")
    if blur is not None and blur >= 0.05:
        filters.append(f"gblur=sigma={blur:.4f}")
    if options.fps:
        filters.append(f"fps={options.fps}")
    if any(item.startswith(("crop", "scale", "rotate", "transpose", "hflip", "vflip")) for item in filters):
        filters.append("setsar=1")
    if include_timing:
        if options.drop_enabled:
            interval = options.drop_interval
            filters.append(f"select='not(eq(mod(n\\,{interval})\\,{interval - 1}))'")
        speed = sampled.get("speed") or 1.0
        tempo = speed * ((options.drop_interval / (options.drop_interval - 1)) if options.drop_enabled else 1.0)
        if abs(tempo - 1) > 1e-4:
            filters.append(f"setpts=PTS/{tempo:.6f}")
    return filters


def _zoom(scale: float, width: int, height: int) -> list[str]:
    if scale > 1:
        sw = max(width, _even(round(width * scale)))
        sh = max(height, _even(round(height * scale)))
        return [f"scale={sw}:{sh}:flags=bicubic", f"crop={width}:{height}"]
    sw = min(width, max(2, _even(round(width * scale))))
    sh = min(height, max(2, _even(round(height * scale))))
    x = _even_floor(max(0, (width - sw) // 2))
    y = _even_floor(max(0, (height - sh) // 2))
    return [f"scale={sw}:{sh}:flags=bicubic", f"pad={width}:{height}:{x}:{y}:color=black"]


def _rotate(angle: float) -> list[str]:
    folded = angle % 360
    if abs(folded) < 0.05 or abs(folded - 360) < 0.05:
        return []
    if abs(folded - 90) < 0.05:
        return ["transpose=1"]
    if abs(folded - 180) < 0.05:
        return ["hflip", "vflip"]
    if abs(folded - 270) < 0.05:
        return ["transpose=2"]
    radians = -angle * math.pi / 180.0
    return [f"rotate={radians:.6f}:ow=iw:oh=ih:c=black"]


def _eq_filter(sampled: dict) -> str:
    brightness = sampled.get("brightness")
    contrast = sampled.get("contrast")
    saturation = sampled.get("saturation")
    if brightness is None and contrast is None and saturation is None:
        return ""
    b = 0.0 if brightness is None else brightness
    c = 1.0 if contrast is None else contrast
    s = 1.0 if saturation is None else saturation
    if abs(b) < 1e-6 and abs(c - 1) < 1e-6 and abs(s - 1) < 1e-6:
        return ""
    return f"eq=brightness={b:.5f}:contrast={c:.5f}:saturation={s:.5f}"


def _active_masks(options: ProcessOptions, preview_time: float | None,
                  width: int, height: int) -> tuple[list[str], int, int]:
    bodies: list[str] = []
    serial = 0
    for mask in options.masks:
        if not _time_active(mask.start, mask.end, preview_time):
            continue
        rect = _pixel_rect(mask.x, mask.y, mask.w, mask.h, width, height)
        enable = _enable_expr(mask.start, mask.end, preview_time, 0.0)
        px, py, pw, ph = rect
        serial += 1
        patch = f"p{serial}"
        base = f"b{serial}"
        out = f"m{serial}"
        if mask.mode == "blur":
            patch_chain = f"crop={pw}:{ph}:{px}:{py},gblur=sigma={mask.blur:.3f}"
        else:
            sx, sy, sw, sh = _pixel_rect(mask.src_x, mask.src_y, mask.src_w, mask.src_h, width, height)
            patch_chain = f"crop={sw}:{sh}:{sx}:{sy}"
            if mask.mode == "flip_h":
                patch_chain += ",hflip"
            elif mask.mode == "flip_v":
                patch_chain += ",vflip"
            if (sw, sh) != (pw, ph):
                patch_chain += f",scale={pw}:{ph}:flags=bicubic"
        patch_chain += _alpha_tail(mask.feather, mask.opacity)
        overlay = f"overlay={px}:{py}"
        if enable and preview_time is None:
            overlay += f":enable='{enable}'"
        bodies.append(
            f"[__SRC__]split[{base}][t{serial}];"
            f"[t{serial}]{patch_chain}[{patch}];"
            f"[{base}][{patch}]{overlay}[{out}]"
        )
    return bodies, width, height


def _alpha_tail(feather: int, opacity: float) -> str:
    parts = []
    if feather > 0 or opacity < 0.999:
        parts.append("format=rgba")
    if feather > 0:
        parts.append(
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='clip(255*min(min(X,Y),min(W-X,H-Y))/{float(feather):.2f},0,255)'"
        )
    if opacity < 0.999:
        parts.append(f"colorchannelmixer=aa={opacity:.4f}")
    return ("," + ",".join(parts)) if parts else ""


def _watermark_filters(mark: WatermarkSpec, width: int, height: int, duration: float,
                       font: str | None, preview_time: float | None, time_origin: float,
                       ) -> tuple[list[str], list[tuple[str, str]], str | None]:
    if not mark.enabled or not _time_active(mark.start, mark.end, preview_time):
        return [], [], None
    if _mark_kind(mark) == "text" and _needs_font(mark) and not font:
        raise ValueError("水印含中文，但没有找到字体。请在水印页指定字体文件，例如微软雅黑。")
    positions = _positions(mark, width, height, preview_time, time_origin)
    enable = _enable_expr(mark.start, mark.end, preview_time, time_origin)
    if abs(mark.angle) < 0.1 and _mark_kind(mark) == "text":
        texts = [
            _drawtext(mark, font, x, y, enable if preview_time is None else None)
            for x, y in positions
        ]
        return [f"[__SRC__]{','.join(texts)}[wtxt]"], [], "wtxt"
    return _layer_watermark(mark, font, positions, enable if preview_time is None else None, duration)


def _layer_watermark(mark: WatermarkSpec, font: str | None, positions: list[tuple[str, str]],
                     enable: str | None, duration: float) -> tuple[list[str], list[tuple[str, str]], str]:
    inputs: list[tuple[str, str]] = []
    opacity = max(0.0, min(1.0, mark.opacity))
    if _mark_kind(mark) == "image":
        inputs.append(("image", mark.image))
        source = "[1:v]"
        body = f"{source}scale={mark.size}:-1,format=rgba"
    else:
        text_w = max(mark.size + 8, int(mark.size * max(1, len(mark.text)) * 1.05) + 16)
        text_h = int(mark.size * 1.8) + 8
        text = _drawtext(mark, font, "(w-text_w)/2", "(h-text_h)/2", None, opacity=1.0)
        life = max(0.5, duration) + 1.0
        body = (f"color=c=black@0:s={text_w}x{text_h}:r=25:d={life:.3f},format=rgba,{text}")
    if abs(mark.angle) >= 0.1:
        radians = -mark.angle * math.pi / 180.0
        body += f",rotate={radians:.6f}:c=none:ow=rotw({radians:.6f}):oh=roth({radians:.6f})"
    body += f",colorchannelmixer=aa={opacity:.4f}"
    count = len(positions)
    if count == 1:
        body += "[wm0]"
        labels = ["wm0"]
    else:
        labels = [f"wm{i}" for i in range(count)]
        body += f",split={count}" + "".join(f"[{label}]" for label in labels)
    segments = [body]
    eval_mode = ":eval=frame" if any(not _numeric(value) for pair in positions for value in pair) else ""
    enable_text = f":enable='{enable}'" if enable else ""
    for index, (x, y) in enumerate(positions):
        out = f"wmout{index}"
        source = "__SRC__" if index == 0 else f"wmout{index - 1}"
        segments.append(
            f"[{source}][{labels[index]}]overlay=x={_quote_expr(x)}:y={_quote_expr(y)}:shortest=1"
            f"{eval_mode}{enable_text}[{out}]"
        )
    return segments, inputs, f"wmout{count - 1}"


def _linear_audio_filters(options: ProcessOptions, sampled: dict, tempo: float, audio_count: int) -> list[str]:
    if audio_count <= 0 and options.bgm_mode == "off":
        return []
    if options.bgm_mode == "replace" or (options.mute and options.bgm_mode != "off"):
        return []
    if audio_count <= 0:
        return []
    filters: list[str] = []
    if options.mute:
        filters.append("volume=0")
        if abs(tempo - 1) > 0.001:
            filters.extend(_atempo(tempo))
        return filters
    volume = sampled.get("volume_db")
    if volume is not None and abs(volume) > 0.001:
        filters.append(f"volume={volume:.4f}dB")
    if abs(tempo - 1) > 0.001:
        filters.extend(_atempo(tempo))
    if options.denoise:
        filters.append(f"afftdn=nr={options.denoise_nr:.2f}")
    if options.eq_enabled:
        filters.append(f"equalizer=f=120:t=q:w=1:g={options.eq_low:.2f}")
        filters.append(f"equalizer=f=1000:t=q:w=1:g={options.eq_mid:.2f}")
        filters.append(f"equalizer=f=8000:t=q:w=1:g={options.eq_high:.2f}")
    if options.loudnorm:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return filters


def _atempo(speed: float) -> list[str]:
    parts: list[str] = []
    remaining = speed
    while remaining > 2.0001:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.4999:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.5f}")
    return parts


def _positions(mark: WatermarkSpec, width: int, height: int,
               preview_time: float | None, origin: float) -> list[tuple[str, str]]:
    x0 = int(round(mark.x * width))
    y0 = int(round(mark.y * height))
    when = preview_time
    if mark.style in {"text", "image", "translucent"}:
        return [(str(x0), str(y0))]
    if mark.style == "move_h":
        return [(_moving(x0, _speed(mark), width, when, origin), str(y0))]
    if mark.style == "move_v":
        return [(str(x0), _moving(y0, _speed(mark), height, when, origin))]
    if mark.style == "path":
        loop = _path_loop(mark)
        if preview_time is not None:
            x, y = path_position(mark.path_points, preview_time, loop, width, height)
            return [(str(int(round(x))), str(int(round(y))))]
        return [
            (_path_expr(mark, "x", width, height, loop, origin),
             _path_expr(mark, "y", width, height, loop, origin)),
        ]
    points = _grid(mark, width, height)
    drift_x = mark.speed if mark.speed > 0 else 0.0
    drift_y = mark.speed * 0.35 if mark.style == "diagonal" and mark.speed > 0 else 0.0
    placed = []
    for x, y in points:
        placed.append((
            _moving(x, drift_x, width, when, origin) if drift_x else str(x),
            _moving(y, drift_y, height, when, origin) if drift_y else str(y),
        ))
    return placed


def _grid(mark: WatermarkSpec, width: int, height: int) -> list[tuple[int, int]]:
    spacing = max(40, mark.spacing)
    points: list[tuple[int, int]] = []
    if mark.style == "diagonal":
        for start in range(-height, width, spacing):
            x, y = start, 0
            while y < height:
                if 0 <= x < width:
                    points.append((int(x), int(y)))
                x += spacing
                y += spacing
    else:
        y = spacing // 3
        while y < height:
            x = spacing // 3
            while x < width:
                points.append((int(x), int(y)))
                x += spacing
            y += spacing
    if len(points) > 36:
        return _grid(replace(mark, spacing=spacing + 80), width, height)
    return points or [(int(mark.x * width), int(mark.y * height))]


def _moving(base: int, speed: float, limit: int, preview_time: float | None, origin: float) -> str:
    limit = max(1, limit)
    if speed <= 0:
        return str(base)
    if preview_time is not None:
        return str(int(round((base + preview_time * speed) % limit)))
    clock = "t" if abs(origin) < 1e-6 else f"(t+{origin:.3f})"
    return f"mod({base}+{clock}*{speed:.3f},{limit})"


def _path_loop(mark: WatermarkSpec) -> float:
    return max(2.0, (len(mark.path_points) - 1) * 500.0 / max(mark.speed, 1.0))


def _path_expr(mark: WatermarkSpec, axis: str, width: int, height: int, loop: float, origin: float) -> str:
    coords = []
    for x, y in mark.path_points:
        coords.append(x * width if axis == "x" else y * height)
    segs = len(coords) - 1
    clock = "t" if abs(origin) < 1e-6 else f"(t+{origin:.3f})"
    travel = f"{segs}*(1-abs(2*mod({clock},{loop:.3f})/{loop:.3f}-1))"
    expr = f"{coords[-2]:.2f}+({coords[-1] - coords[-2]:.2f})*({travel}-{segs - 1})"
    for index in range(segs - 2, -1, -1):
        start, end = coords[index], coords[index + 1]
        expr = f"if(lt({travel},{index + 1}),{start:.2f}+({end - start:.2f})*({travel}-{index}),{expr})"
    return expr


def _drawtext(mark: WatermarkSpec, font: str | None, x: str, y: str, enable: str | None,
              opacity: float | None = None) -> str:
    alpha = mark.opacity if opacity is None else opacity
    parts = []
    if font:
        parts.append(f"fontfile='{_escape(font)}'")
    parts.extend([
        f"text='{_escape(mark.text)}'",
        f"fontsize={mark.size}",
        f"fontcolor=white@{alpha:.3f}",
        "borderw=1",
        f"bordercolor=black@{min(0.65, max(alpha, 0.25)):.3f}",
        f"x={_quote_expr(x)}",
        f"y={_quote_expr(y)}",
    ])
    if enable:
        parts.append(f"enable='{enable}'")
    return "drawtext=" + ":".join(parts)


def _time_active(start: float, end: float, preview_time: float | None) -> bool:
    if preview_time is None:
        return True
    if preview_time + 1e-3 < start:
        return False
    if end > 0 and preview_time - 1e-3 > end:
        return False
    return True


def _enable_expr(start: float, end: float, preview_time: float | None, origin: float) -> str | None:
    if preview_time is not None or (start <= 0 and end <= 0):
        return None
    begin = start - origin
    finish = end - origin if end > 0 else None
    if finish is None:
        return None if begin <= 0 else f"gte(t,{begin:.3f})"
    return f"between(t,{begin:.3f},{finish:.3f})"


def _pixel_rect(x: float, y: float, w: float, h: float, width: int, height: int) -> tuple[int, int, int, int]:
    px = min(width - 2, max(0, int(round(x * width))))
    py = min(height - 2, max(0, int(round(y * height))))
    pw = max(2, int(round(w * width)))
    ph = max(2, int(round(h * height)))
    px, py = _even_floor(px), _even_floor(py)
    pw, ph = _even(pw), _even(ph)
    if px + pw > width:
        pw = _even(width - px)
    if py + ph > height:
        ph = _even(height - py)
    if pw < 2 or ph < 2:
        raise ValueError("蒙版区域超出画面或小于 2 像素")
    return px, py, pw, ph


def _codec_args(options: ProcessOptions) -> list[str]:
    if options.codec in {"libx264", "libx265"}:
        args = ["-c:v", options.codec, "-preset", options.encoder_preset,
                "-crf", str(options.crf), "-pix_fmt", "yuv420p"]
        if options.codec == "libx265":
            args += ["-tag:v", "hvc1"]
        return args
    mapped = {"ultrafast": "p1", "superfast": "p2", "veryfast": "p3", "faster": "p3",
              "fast": "p4", "medium": "p5", "slow": "p6"}[options.encoder_preset]
    args = ["-c:v", options.codec, "-preset", mapped, "-rc", "vbr", "-cq", str(options.crf),
            "-b:v", "0", "-pix_fmt", "yuv420p"]
    if options.codec == "hevc_nvenc":
        args += ["-tag:v", "hvc1"]
    return args


def _resolve_font(options: ProcessOptions) -> str | None:
    for candidate in (options.watermark.font, options.font):
        if candidate and Path(candidate).is_file():
            return candidate
    return find_cjk_font()


def _mark_kind(mark: WatermarkSpec) -> str:
    if mark.style == "image":
        return "image"
    if mark.style == "translucent" and not mark.text.strip() and mark.image:
        return "image"
    return "text"


def _needs_font(mark: WatermarkSpec) -> bool:
    return _mark_kind(mark) == "text" and any(ord(ch) > 127 for ch in mark.text)


def _speed(mark: WatermarkSpec) -> float:
    return mark.speed if mark.speed > 0 else 60.0


def _style_name(style: str) -> str:
    return {
        "text": "固定文字", "image": "图片", "translucent": "半透明", "tile": "平铺",
        "diagonal": "斜向", "move_h": "横向移动", "move_v": "纵向移动", "path": "轨迹",
    }.get(style, style)


def _codec_name(codec: str) -> str:
    return {"libx264": "H.264", "libx265": "H.265", "h264_nvenc": "NVENC H.264",
            "hevc_nvenc": "NVENC H.265"}.get(codec, codec)


def _even(value: int) -> int:
    value = int(value)
    if value <= 2:
        return 2
    return value - (value % 2)


def _even_floor(value: int) -> int:
    return max(0, int(value) - (int(value) % 2))


def _numeric(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", value))


def _quote_expr(value: str) -> str:
    if _numeric(value) or value in {"(w-text_w)/2", "(h-text_h)/2"}:
        return value
    escaped = value.replace("\\", r"\\").replace(",", r"\,").replace(":", r"\:")
    return f"'{escaped}'"


def _escape(value: str) -> str:
    return (value.replace("\\", "/")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace("%", r"\%")
            .replace(",", r"\,")
            .replace("[", r"\[")
            .replace("]", r"\]")
            .replace(";", r"\;"))


def _run(command: list[str], label: str) -> None:
    try:
        proc = subprocess.run(command, capture_output=True, timeout=180, creationflags=_flags())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"{label}: {exc}") from exc
    if proc.returncode:
        tail = (proc.stderr or b"")[-600:].decode("utf-8", "replace").strip()
        raise MediaError(f"{label}: {tail or proc.returncode}")


