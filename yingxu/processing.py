"""Turn ProcessOptions into a real FFmpeg filter graph. Disabled features are omitted."""
from __future__ import annotations

import json
import math
import random
import re
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from .engine import MediaError, _flags
from .models import (
    ENCODER_PRESETS, MASK_MODES, PIP_POSITIONS, VIDEO_CODECS, WATERMARK_STYLES,
    MaskSpec, PhotoInsertSpec, PipSpec, Preset, ProcessOptions, RangeSpec, WatermarkSpec,
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
    seek_output: bool = False


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
    _validate_pip(options.pip)
    _validate_photos(options.photos)
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
    if options.pip.enabled:
        lines.append("画中画" if options.pip.path else "画中画（未选视频）")
    if options.photos.enabled:
        lines.append(f"照片加帧 {len(options.photos.paths)} 张" if options.photos.paths else "照片加帧（未加照片）")
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
    if options.pip.enabled and options.pip.audio_percent > 0:
        lines.append(f"画中画会按 {options.pip.audio_percent:.0f}% 混入叠加视频的声音。")
    if options.photos.enabled:
        lines.append("照片加帧会拉长视频，并在原镜头之间插入这些照片。")
    return lines


def build_plan(options: ProcessOptions, sampled: dict[str, float | None], media: dict, *,
               preview_time: float | None = None, time_origin: float = 0.0,
               include_timing: bool = True, include_audio: bool = True, seed: int = 0) -> FilterPlan:
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
    main_duration = duration / tempo
    variation = dedup_sample(options, seed)
    photo_times = []
    if options.photos.enabled and options.photos.paths and preview_time is None:
        photo_times = photo_insert_times(main_duration, variation["photo_interval"], variation["photo_phase"])
        if not photo_times:
            raise ValueError("视频太短，按当前间隔插不进照片。请缩短间隔或换更长的视频。")
        if len(photo_times) > 240:
            raise ValueError("插入次数超过 240 次，请加大间隔。")
    expected = main_duration + len(photo_times) * variation["photo_hold"]
    out_w, out_h = _output_size(width, height, options, sampled)
    font = _resolve_font(options)
    video_filters = _linear_video_filters(options, sampled, width, height, include_timing=include_timing and preview_time is None)
    # 蒙版/水印的开始结束时间按原片时间轴填写；变速或抽帧后换算到成片时间轴。
    time_scale = tempo if (include_timing and preview_time is None) else 1.0
    mask_filters, out_w, out_h = _active_masks(options, preview_time, out_w, out_h, time_origin, time_scale)
    wm_filters, wm_inputs, wm_label = _watermark_filters(
        options.watermark, out_w, out_h, main_duration, font, preview_time, time_origin, time_scale,
    )
    pip_active = _pip_active(options.pip, preview_time)
    audio_count = int(media.get("audio_count") or 0)
    audio_filters = []
    use_bgm = False
    if include_audio:
        audio_filters = _linear_audio_filters(options, sampled, tempo if include_timing else 1.0, audio_count)
        use_bgm = options.bgm_mode != "off"
    needs_graph = bool(video_filters or mask_filters or wm_filters or audio_filters or use_bgm or pip_active or photo_times)
    plan = FilterPlan(expected_duration=expected, force_verify=_needs_forced_verify(options, sampled) or bool(photo_times),
                      seek_output=bool(photo_times))
    plan.parameters = _parameters(options, sampled, tempo, expected, font)
    if options.pip.enabled or options.photos.enabled:
        plan.parameters["dedup"] = variation
        if photo_times:
            plan.parameters["photo_inserts"] = photo_times
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
    plan.extra_inputs.extend(wm_inputs)
    if pip_active:
        pip_body, pip_input, pip_audio = _pip_overlay(
            options.pip, variation, current, out_w, out_h, main_duration, preview_time,
            len(plan.extra_inputs) + 1, time_scale,
        )
        segments.append(pip_body)
        current = pip_body.rsplit("[", 1)[-1].rstrip("]")
        plan.extra_inputs.append(pip_input)
        plan.parameters["pip"] = {
            "opacity": variation["pip_opacity"], "size": variation["pip_size"],
            "position": options.pip.position, "audio_percent": options.pip.audio_percent,
            "short_mode": options.pip.short_mode,
        }
    else:
        pip_audio = None
    chain("format=yuv420p")
    plan.vout = current

    bgm_mode = "replace" if options.mute and options.bgm_mode == "mix" else options.bgm_mode
    if bgm_mode != "off":
        bgm_index = 1 + len(plan.extra_inputs)
        plan.extra_inputs.append(("bgm", options.bgm_path))
        bgm = (f"[{bgm_index}:a]atrim=0:{max(0.1, main_duration):.3f},asetpts=PTS-STARTPTS,"
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
    if pip_audio and include_audio:
        _mix_pip_audio(segments, plan, pip_audio, audio_count, main_duration)
    if photo_times:
        _insert_photos(segments, plan, options.photos, photo_times, variation, out_w, out_h,
                       main_duration, float(media.get("fps") or 25), bool(audio_count and include_audio))
    plan.graph = ";".join(segments)
    return plan


def command_from_plan(ffmpeg: str, source: str, output: str, plan: FilterPlan,
                      media: dict, options: ProcessOptions, *,
                      still_time: float | None = None,
                      clip: tuple[float, float] | None = None) -> list[str]:
    preview = still_time is not None or clip is not None
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y" if preview else "-n"]
    if clip is not None and not plan.seek_output:
        cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1]:.3f}"]
    elif still_time is not None:
        cmd += ["-ss", f"{still_time:.3f}"]
    cmd += ["-i", source]
    for item in plan.extra_inputs:
        kind, path = item[0], item[1]
        seek = item[2] if len(item) > 2 and item[2] is not None else None
        if seek is not None:
            cmd += ["-ss", f"{float(seek):.3f}"]
        if kind in {"image", "photo"}:
            cmd += ["-framerate", "25", "-loop", "1", "-i", path]
        elif kind == "pip_hold" or (kind == "pip_loop" and seek is not None):
            cmd += ["-i", path]
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
    elif plan.seek_output:
        cmd += ["-ss", f"{clip[0]:.3f}", "-t", f"{clip[1]:.3f}"]
    cmd += ["-f", "mp4", output]
    return cmd


def prepare_encode(job, src: dict, ffmpeg: str, temp: Path) -> tuple[list[str], dict, float, bool]:
    options = job.options
    if not isinstance(options, ProcessOptions):
        raise TypeError("任务缺少处理方案")
    sampled = sample_options(options, job.derived_seed())
    plan = build_plan(options, sampled, src, seed=job.derived_seed())
    command = command_from_plan(ffmpeg, str(job.source), str(temp), plan, src, options)
    return command, plan.parameters, plan.expected_duration, plan.force_verify


def default_logo_path() -> Path:
    return Path(__file__).resolve().parent / "assets" / "default_logo.png"


def apply_default_logo(options: ProcessOptions) -> ProcessOptions:
    """Image watermarks with no file use the built-in logo, including export."""
    mark = options.watermark
    if not mark.enabled or mark.style != "image":
        return options
    if mark.image and Path(mark.image).is_file():
        return options
    return replace(options, watermark=replace(mark, image=str(default_logo_path())))


def visual_effects(options: ProcessOptions) -> bool:
    """True when the current frame can look different from the source."""
    if options.mirror_h or options.mirror_v or options.crop_enabled or options.fps:
        return True
    for name in ("scale", "rotate", "brightness", "contrast", "saturation", "color_temp", "sharpen", "blur"):
        if getattr(options, name).enabled:
            return True
    if options.watermark.enabled:
        return True
    if options.masks:
        return True
    if options.pip.enabled and options.pip.path:
        return True
    if options.photos.enabled and options.photos.paths:
        return True
    return False


def prepare_preview_options(options: ProcessOptions, draft_mask: MaskSpec | None = None,
                            include_draft: bool = False) -> ProcessOptions:
    """Drop incomplete pieces so one empty field does not freeze the whole preview."""
    options = apply_default_logo(options)
    mark = options.watermark
    if mark.enabled:
        kind = _mark_kind(mark)
        if kind == "text" and not mark.text.strip():
            mark = replace(mark, enabled=False)
        elif mark.style == "path" and len(mark.path_points) < 2:
            mark = replace(mark, enabled=False)
        elif kind == "image" and not (mark.image and Path(mark.image).is_file()):
            mark = replace(mark, enabled=False)
    pip = options.pip
    if pip.enabled and not (pip.path and Path(pip.path).is_file()):
        pip = replace(pip, enabled=False)
    photos = options.photos
    if photos.enabled:
        paths = tuple(path for path in photos.paths if Path(path).is_file())
        photos = replace(photos, enabled=bool(paths), paths=paths)
    masks = list(options.masks)
    if include_draft and draft_mask is not None and not any(_mask_same(item, draft_mask) for item in masks):
        masks.append(draft_mask)
    return replace(options, watermark=mark, pip=pip, photos=photos, masks=tuple(masks))


def _mask_same(left: MaskSpec, right: MaskSpec) -> bool:
    return (left.mode == right.mode and abs(left.x - right.x) < 0.005 and abs(left.y - right.y) < 0.005
            and abs(left.w - right.w) < 0.005 and abs(left.h - right.h) < 0.005
            and abs(left.start - right.start) < 0.05 and abs(left.end - right.end) < 0.05)


def render_still(ffmpeg: str, source: Path, media: dict, options: ProcessOptions,
                 time_sec: float, output: Path, seed: int, max_edge: int = 960) -> None:
    media, options = _fit_preview(media, options, max_edge)
    sampled = sample_options(options, seed)
    source_time = max(0.0, time_sec)
    # 时间轴按原片拖动。停在某个插入点之后的停留时间内时，静帧显示那张照片。
    if options.photos.enabled and options.photos.paths:
        variation = dedup_sample(options, seed)
        times = photo_insert_times(float(media["duration"]), variation["photo_interval"], variation["photo_phase"])
        for index, point in enumerate(times):
            if point <= source_time < point + variation["photo_hold"]:
                photo = options.photos.paths[index % len(options.photos.paths)]
                width, height = int(media["width"]), int(media["height"])
                render_photo_frame(ffmpeg, photo, width, height, variation["photo_hold"], source_time - point,
                                   options.photos.fade_ms / 1000, output)
                return
    plan = build_plan(options, sampled, media, preview_time=source_time, include_timing=False,
                      include_audio=False, seed=seed)
    command = command_from_plan(ffmpeg, str(source), str(output), plan, media, options, still_time=source_time)
    _run(command, "预览帧失败")


def _fit_preview(media: dict, options: ProcessOptions, max_edge: int) -> tuple[dict, ProcessOptions]:
    width, height = int(media["width"]), int(media["height"])
    longest = max(width, height)
    if max_edge <= 0 or longest <= max_edge or longest < 2:
        return media, options
    scale = max_edge / longest
    fitted = dict(media)
    fitted["width"] = _even(max(64, round(width * scale)))
    fitted["height"] = _even(max(64, round(height * scale)))
    sx = fitted["width"] / width
    sy = fitted["height"] / height
    shrink = min(sx, sy)
    if options.crop_enabled:
        crop_x = min(fitted["width"] - 16, _even_floor(int(options.crop_x * sx)))
        crop_y = min(fitted["height"] - 16, _even_floor(int(options.crop_y * sy)))
        crop_w = max(16, _even(int(options.crop_w * sx)))
        crop_h = max(16, _even(int(options.crop_h * sy)))
        crop_w = min(crop_w, fitted["width"] - crop_x)
        crop_h = min(crop_h, fitted["height"] - crop_y)
        options = replace(options, crop_x=crop_x, crop_y=crop_y, crop_w=_even(crop_w), crop_h=_even(crop_h))
    mark = options.watermark
    options = replace(options, watermark=replace(
        mark,
        size=max(8, int(round(mark.size * shrink))),
        spacing=max(40, int(round(mark.spacing * shrink))),
        speed=mark.speed * shrink,
    ))
    return fitted, options


def render_clip(ffmpeg: str, source: Path, media: dict, options: ProcessOptions,
                start: float, length: float, output: Path, seed: int) -> None:
    sampled = sample_options(options, seed)
    plan = build_plan(options, sampled, media, time_origin=start, include_timing=True, seed=seed)
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


def adjacent_source(x: float, y: float, w: float, h: float, gap: float = 0.01) -> tuple[float, float, float, float]:
    """Pick a same-size copy source right below the box, or above it when there is no room."""
    w = max(0.01, min(1.0, w))
    h = max(0.01, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    below = y + h + gap
    if below + h <= 1.0:
        return x, below, w, h
    above = y - gap - h
    if above >= 0.0:
        return x, above, w, h
    return x, max(0.0, min(1.0 - h, y)), w, h


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


def dedup_sample(options: ProcessOptions, seed: int) -> dict:
    """Independent random stream so picture ranges stay stable when dedup jitter is used."""
    rng = random.Random((int(seed) + 917003) & ((1 << 63) - 1))
    pip = options.pip
    photos = options.photos
    opacity = _clamp(pip.opacity + rng.uniform(-pip.opacity_jitter, pip.opacity_jitter), 0.0, 1.0)
    jx = rng.uniform(-pip.position_jitter, pip.position_jitter)
    jy = rng.uniform(-pip.position_jitter, pip.position_jitter)
    size = _clamp(pip.size + rng.uniform(-pip.size_jitter, pip.size_jitter), 0.05, 0.8)
    interval = _clamp(photos.interval + rng.uniform(-photos.interval_jitter, photos.interval_jitter), 0.2, 120.0)
    hold_ms = _clamp(photos.hold_ms + rng.uniform(-photos.hold_jitter_ms, photos.hold_jitter_ms), 40.0, 5000.0)
    phase_span = min(0.5, max(0.0, photos.interval_jitter))
    phase = rng.uniform(0.0, phase_span) if phase_span > 0 else 0.0
    return {
        "pip_opacity": round(opacity, 5),
        "pip_jx": round(jx, 5),
        "pip_jy": round(jy, 5),
        "pip_size": round(size, 5),
        "photo_interval": round(interval, 5),
        "photo_hold": round(hold_ms / 1000.0, 5),
        "photo_phase": round(phase, 5),
    }


def photo_insert_times(main_duration: float, interval: float, phase: float) -> list[float]:
    """Source-timeline points, after speed change, where a photo is inserted."""
    if interval <= 0 or main_duration <= 0:
        return []
    times: list[float] = []
    cursor = interval + max(0.0, phase)
    guard = 0
    while cursor < main_duration - 0.05 and guard < 500:
        times.append(round(cursor, 3))
        cursor += interval
        guard += 1
    return times


def locate_output_time(output_t: float, main_duration: float, inserts: list[float], hold: float):
    """Map an output timestamp to main content or a photo insert."""
    output_t = max(0.0, output_t)
    source = 0.0
    elapsed = 0.0
    for index, point in enumerate(inserts):
        span = max(0.0, point - source)
        if output_t <= elapsed + span + 1e-4:
            return "main", source + max(0.0, output_t - elapsed)
        elapsed += span
        if output_t <= elapsed + hold + 1e-4:
            return "photo", (index, max(0.0, output_t - elapsed))
        elapsed += hold
        source = point
    tail = source + max(0.0, output_t - elapsed)
    return "main", min(main_duration, tail)


def output_duration(main_duration: float, inserts: list[float], hold: float) -> float:
    return main_duration + len(inserts) * hold


def render_photo_frame(ffmpeg: str, photo: str, width: int, height: int, hold: float,
                       local: float, fade: float, output: Path) -> None:
    width, height = _even(width), _even(height)
    local = min(max(0.0, local), max(0.0, hold - 0.02))
    fade = min(max(0.0, fade), max(0.0, hold / 2 - 0.02))
    fade_filter = ""
    if fade > 0.01:
        fade_filter = f",fade=t=in:st=0:d={fade:.3f},fade=t=out:st={max(fade, hold - fade):.3f}:d={fade:.3f}"
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps=25,format=yuv420p{fade_filter}"
    )
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-i", str(photo),
               "-vf", video_filter, "-ss", f"{local:.3f}", "-frames:v", "1", "-an", str(output)]
    _run(command, "照片预览失败")


def _needs_forced_verify(options: ProcessOptions, sampled: dict) -> bool:
    speed = sampled.get("speed")
    return options.drop_enabled or options.photos.enabled or (speed is not None and abs(speed - 1) > 1e-3)


def _validate_pip(pip: PipSpec) -> None:
    if not pip.enabled:
        return
    if pip.position not in PIP_POSITIONS:
        raise ValueError("画中画位置无效")
    if pip.short_mode not in {"loop", "hold"}:
        raise ValueError("画中画短片方式无效")
    if not pip.path or not Path(pip.path).is_file():
        raise ValueError("请选择存在的画中画视频")
    if not 0.05 <= pip.size <= 0.8:
        raise ValueError("画中画尺寸必须是画面宽度的 5%～80%")
    if not 0.0 <= pip.opacity <= 1.0 or not 0.0 <= pip.opacity_jitter <= 0.5:
        raise ValueError("画中画透明度或浮动超出范围")
    if not 0.0 <= pip.position_jitter <= 0.2 or not 0.0 <= pip.size_jitter <= 0.2:
        raise ValueError("画中画位置或尺寸浮动超出范围")
    if not 0.0 <= pip.audio_percent <= 100.0:
        raise ValueError("画中画音量必须在 0～100% 之间")
    if pip.start < 0 or (pip.end > 0 and pip.end <= pip.start):
        raise ValueError("画中画出现时间无效")


def _validate_photos(spec: PhotoInsertSpec) -> None:
    if not spec.enabled:
        return
    if not spec.paths:
        raise ValueError("请至少添加一张照片")
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    for raw in spec.paths:
        path = Path(raw)
        if not path.is_file():
            raise ValueError(f"找不到照片: {raw}")
        if path.suffix.lower() not in allowed:
            raise ValueError(f"照片格式不支持: {path.name}")
    if not 0.2 <= spec.interval <= 120:
        raise ValueError("插入间隔必须在 0.2～120 秒之间")
    if not 0.0 <= spec.interval_jitter <= 30:
        raise ValueError("间隔浮动超出范围")
    if not 40 <= spec.hold_ms <= 5000:
        raise ValueError("照片停留时间必须在 40～5000 毫秒之间")
    if not 0.0 <= spec.hold_jitter_ms <= 2000:
        raise ValueError("停留浮动超出范围")
    if not 0.0 <= spec.fade_ms <= 2000:
        raise ValueError("淡入淡出必须在 0～2000 毫秒之间")
    if spec.hold_jitter_ms == 0 and spec.fade_ms * 2 >= spec.hold_ms:
        raise ValueError("淡入淡出需要短于停留时间的一半")


def _pip_active(pip: PipSpec, preview_time: float | None) -> bool:
    if not pip.enabled or not pip.path:
        return False
    return _time_active(pip.start, pip.end, preview_time)


def _pip_overlay(pip: PipSpec, variation: dict, current: str, width: int, height: int,
                 main_duration: float, preview_time: float | None, input_index: int,
                 time_scale: float = 1.0):
    opacity = variation["pip_opacity"]
    size = variation["pip_size"]
    pip_w = _even(max(32, round(width * size)))
    scale = time_scale if time_scale > 1e-6 else 1.0
    # 静帧没有变速，时间按原片；成片里的出现时间要换到变速之后的时间轴。
    if preview_time is None:
        start = max(0.0, pip.start) / scale
        end = pip.end / scale if pip.end > 0 else main_duration
    else:
        start = max(0.0, pip.start)
        end = pip.end if pip.end > 0 else main_duration
    visible = max(0.08, end - start)
    jx = int(round(variation["pip_jx"] * width))
    jy = int(round(variation["pip_jy"] * height))
    margin = max(0, int(round(0.03 * width)))
    x_expr, y_expr = _pip_xy(pip.position, margin, jx, jy)
    seek = None
    if preview_time is None:
        extend = ""
        if pip.short_mode == "hold":
            extend = f",tpad=stop_mode=clone:stop_duration={visible + 0.5:.3f}"
        delay = f",setpts=PTS-STARTPTS+{start:.3f}/TB" if start > 0.001 else ",setpts=PTS-STARTPTS"
        prepared = (
            f"[{input_index}:v]scale={pip_w}:-2,format=rgba,colorchannelmixer=aa={opacity:.4f}"
            f"{extend},trim=duration={visible:.3f}{delay}[pipv]"
        )
        enable = f":enable='between(t,{start:.3f},{end:.3f})'" if start > 0.001 or pip.end > 0 else ""
        kind = "pip_loop" if pip.short_mode != "hold" else "pip_hold"
    else:
        seek = max(0.0, preview_time - start)
        prepared = (
            f"[{input_index}:v]scale={pip_w}:-2,format=rgba,colorchannelmixer=aa={opacity:.4f},"
            f"trim=duration=0.08[pipv]"
        )
        enable = ""
        kind = "pip_hold"
    segment = f"{prepared};[{current}][pipv]overlay=x={x_expr}:y={y_expr}:eof_action=pass{enable}[pipout]"
    audio = None
    if pip.audio_percent > 0 and preview_time is None:
        audio = {"index": input_index, "start": start, "visible": visible,
                 "volume": pip.audio_percent / 100.0, "path": pip.path}
    return segment, (kind, pip.path, seek), audio


def _pip_xy(position: str, margin: int, jx: int, jy: int) -> tuple[str, str]:
    if position == "tl":
        return _shift(str(margin), jx), _shift(str(margin), jy)
    if position == "tr":
        return _shift(f"W-w-{margin}", jx), _shift(str(margin), jy)
    if position == "bl":
        return _shift(str(margin), jx), _shift(f"H-h-{margin}", jy)
    if position == "br":
        return _shift(f"W-w-{margin}", jx), _shift(f"H-h-{margin}", jy)
    return _shift("(W-w)/2", jx), _shift("(H-h)/2", jy)


def _shift(base: str, pixels: int) -> str:
    if pixels == 0:
        return base
    sign = "+" if pixels > 0 else "-"
    return f"{base}{sign}{abs(pixels)}"


def _mix_pip_audio(segments: list[str], plan: FilterPlan, audio: dict, audio_count: int, main_duration: float) -> None:
    if not _media_has_audio(audio["path"]):
        plan.parameters["pip_audio"] = "叠加视频没有音轨，已按静音处理"
        return
    delay = ""
    if audio["start"] > 0.001:
        milliseconds = int(round(audio["start"] * 1000))
        delay = f",adelay={milliseconds}|{milliseconds}"
    segments.append(
        f"[{audio['index']}:a]atrim=0:{audio['visible']:.3f},asetpts=PTS-STARTPTS,"
        f"volume={audio['volume']:.4f}{delay},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[pipa]"
    )
    if plan.aout or audio_count:
        base = plan.aout or "0:a:0"
        segments.append(f"[{base}]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[pipbase]")
        segments.append("[pipbase][pipa]amix=inputs=2:duration=first:normalize=0[pipmix]")
        plan.aout = "pipmix"
        plan.map_source_audio = False
    else:
        segments.append(f"[pipa]apad,atrim=0:{max(0.1, main_duration):.3f}[pipmix]")
        plan.aout = "pipmix"
    plan.has_audio = True


def _insert_photos(segments: list[str], plan: FilterPlan, spec: PhotoInsertSpec, times: list[float],
                   variation: dict, width: int, height: int, main_duration: float, fps: float,
                   want_audio: bool) -> None:
    hold = max(0.04, variation["photo_hold"])
    fade = min(spec.fade_ms / 1000.0, max(0.0, hold / 2 - 0.02))
    fps_i = int(round(fps)) if fps > 1 else 25
    if fps_i not in {24, 25, 30, 50, 60}:
        fps_i = 25
    width, height = _even(width), _even(height)
    count = len(times)
    bounds = [0.0, *times, main_duration]
    split_labels = "".join(f"[ms{i}]" for i in range(count + 1))
    segments.append(f"[{plan.vout}]fps={fps_i},setsar=1,format=yuv420p,split={count + 1}{split_labels}")
    for index in range(count + 1):
        segments.append(
            f"[ms{index}]trim=start={bounds[index]:.3f}:end={bounds[index + 1]:.3f},setpts=PTS-STARTPTS[mv{index}]"
        )
    sequence = [spec.paths[index % len(spec.paths)] for index in range(count)]
    order: list[str] = []
    uses: dict[str, int] = {}
    for path in sequence:
        uses[path] = uses.get(path, 0) + 1
        if path not in order:
            order.append(path)
    labels: dict[str, list[str]] = {}
    for path_index, path in enumerate(order):
        input_index = 1 + len(plan.extra_inputs)
        plan.extra_inputs.append(("photo", path))
        fade_filter = ""
        if fade > 0.01:
            fade_filter = f",fade=t=in:st=0:d={fade:.3f},fade=t=out:st={max(fade, hold - fade):.3f}:d={fade:.3f}"
        prepared = (
            f"[{input_index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps_i},format=yuv420p,"
            f"trim=duration={hold:.3f}{fade_filter},setpts=PTS-STARTPTS"
        )
        names = [f"ph{path_index}_{slot}" for slot in range(uses[path])]
        if uses[path] == 1:
            segments.append(f"{prepared}[{names[0]}]")
        else:
            segments.append(f"{prepared},split={uses[path]}" + "".join(f"[{name}]" for name in names))
        labels[path] = names
    cursor = {path: 0 for path in order}
    photo_labels = []
    for path in sequence:
        photo_labels.append(labels[path][cursor[path]])
        cursor[path] += 1
    use_audio = want_audio and (plan.aout or plan.map_source_audio or plan.has_audio)
    if use_audio:
        base = plan.aout or "0:a:0"
        audio_labels = "".join(f"[ma{i}]" for i in range(count + 1))
        segments.append(
            f"[{base}]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,asplit={count + 1}{audio_labels}"
        )
        for index in range(count + 1):
            segments.append(
                f"[ma{index}]atrim=start={bounds[index]:.3f}:end={bounds[index + 1]:.3f},asetpts=PTS-STARTPTS[mc{index}]"
            )
        silence = (
            f"anullsrc=r=48000:cl=stereo,atrim=duration={hold:.3f},asetpts=PTS-STARTPTS"
        )
        if count == 1:
            segments.append(f"{silence}[sil0]")
        else:
            segments.append(f"{silence},asplit={count}" + "".join(f"[sil{i}]" for i in range(count)))
        pieces = []
        for index, photo_label in enumerate(photo_labels):
            pieces.extend((f"[mv{index}]", f"[mc{index}]", f"[{photo_label}]", f"[sil{index}]"))
        pieces.extend((f"[mv{count}]", f"[mc{count}]"))
        segments.append("".join(pieces) + f"concat=n={count * 2 + 1}:v=1:a=1[catv][cata]")
        plan.vout = "catv"
        plan.aout = "cata"
        plan.map_source_audio = False
        plan.has_audio = True
        return
    pieces = []
    for index, photo_label in enumerate(photo_labels):
        pieces.extend((f"[mv{index}]", f"[{photo_label}]"))
    pieces.append(f"[mv{count}]")
    segments.append("".join(pieces) + f"concat=n={count * 2 + 1}:v=1:a=0[catv]")
    plan.vout = "catv"
    plan.has_audio = False
    plan.map_source_audio = False
    plan.aout = ""


def _media_has_audio(path: str) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30, creationflags=_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if proc.returncode:
        return True
    return bool((proc.stdout or "").strip())


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


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
        filters.append(f"colortemperature=temperature={kelvin:.1f}:pl=1")
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
                  width: int, height: int, origin: float = 0.0,
                  time_scale: float = 1.0) -> tuple[list[str], int, int]:
    bodies: list[str] = []
    serial = 0
    for mask in options.masks:
        if not _time_active(mask.start, mask.end, preview_time):
            continue
        rect = _pixel_rect(mask.x, mask.y, mask.w, mask.h, width, height)
        enable = _enable_expr(mask.start, mask.end, preview_time, origin, time_scale)
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
                       time_scale: float = 1.0,
                       ) -> tuple[list[str], list[tuple[str, str]], str | None]:
    if not mark.enabled or not _time_active(mark.start, mark.end, preview_time):
        return [], [], None
    if _mark_kind(mark) == "text" and _needs_font(mark) and not font:
        raise ValueError("水印含中文，但没有找到字体。请在水印页指定字体文件，例如微软雅黑。")
    positions = _positions(mark, width, height, preview_time, time_origin)
    enable = _enable_expr(mark.start, mark.end, preview_time, time_origin, time_scale)
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


def _enable_expr(start: float, end: float, preview_time: float | None, origin: float,
                 time_scale: float = 1.0) -> str | None:
    if preview_time is not None or (start <= 0 and end <= 0):
        return None
    scale = time_scale if time_scale > 1e-6 else 1.0
    begin = (start - origin) / scale
    finish = (end - origin) / scale if end > 0 else None
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


