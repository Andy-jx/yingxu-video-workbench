# 影序 · 视频处理工作台

基于 **Python + PySide6 + FFmpeg / FFprobe** 的本地视频批量处理工作台。中文图形界面和命令行共用同一套编码引擎，不依赖云端 API。

> **范围声明**：本工具做画面、音频、水印和区域蒙版处理，不保证任何平台把成片当成“原创”，也不把文件变化说成内容原创。处理他人素材前请确认有权使用。不会重排或删除镜头。

## 当前已实现

- 三种模式：单视频多版本、批量素材、成片二次处理。成片模式锁定镜像、旋转、裁剪和抽帧。
- 版本数、随机种子、预设。每个功能独立开关；启用的范围按种子抽样，关闭的功能不会进入 FFmpeg。
- 批量队列：进度、暂停/继续（含当前 FFmpeg 进程）、取消选中、取消全部、失败或取消后重试。
- 画面：缩放、裁剪、镜像、旋转、亮度、对比度、饱和度、色温、锐化、模糊、变速、抽帧、帧率、H.264 / H.265。镜像、旋转、抽帧默认关闭。变速和抽帧会强制完整解码校验，音频用 atempo 跟随时长。
- 音频：音量、三段均衡、afftdn 降噪、响度标准化、原声静音、背景音乐混合或替换。有滤镜时处理第一条音轨。
- 水印八种都走真实滤镜：固定文字、图片、半透明、平铺、斜向、横向移动、纵向移动、折线轨迹。可调位置、大小、透明度、角度、速度、时间、间距。
- 字幕蒙版：时间段、相对坐标、预览框选、模糊、局部复制、只翻转蒙版层、羽化。可把当前参数单独套到选中素材。复杂背景不会被说成无痕修复。
- 独立导出目录，不覆盖原片，不覆盖已有成品。默认完整解码校验。FFmpeg / FFprobe 默认走 PATH，路径在「导出 → 高级」。
- 预设可保存，项目可存成 JSON，任务结果写入本机 SQLite。处理完成后可查看该版本实际参数。

**这一版没有做，界面里也没有对应按钮：** OpenCV 画面检测、自动寻找无字幕区域、徒手绘制水印曲线、图形多段均衡、音频闪避、安装包、以及任何“智能去字幕 / 镜头重组”。NVIDIA NVENC 只有当前 FFmpeg 带该编码器时才会出现，没有显卡时任务会失败并记入日志。`packaging/yingxu.spec` 是 PyInstaller 脚本，本仓库不附带已经打好的 exe。

## 目录

```text
yingxu-video-workbench/
├── yingxu/
│   ├── __init__.py
│   ├── __main__.py         # python -m yingxu（GUI）/ process（CLI）
│   ├── gui.py              # PySide6 中文工作台
│   ├── cli.py              # 命令行
│   ├── queue.py            # 共用任务队列：进度、暂停、取消、重试
│   ├── engine.py           # FFmpeg 调用与导出校验
│   ├── processing.py       # 画面、音频、水印、蒙版滤镜
│   ├── models.py           # 任务、预设和处理参数
│   ├── store.py            # 预设副本和任务记录
│   └── extensions.py       # 水印 / 蒙版滤镜入口
├── presets/
│   ├── 只转码.json
│   ├── 轻度处理.json
│   └── 标准处理.json
├── packaging/
│   └── yingxu.spec         # PyInstaller 脚本，不是现成安装包
├── tests/
│   ├── test_core.py
│   ├── test_processing.py
│   ├── test_gui.py
│   └── test_integration.py
├── requirements.txt
└── README.md
```

## Windows 10 / 11 安装依赖

1. 安装 **Python 3.10 或更新版**（推荐 Python 3.11/3.12，64 位）。勾选“Add Python to PATH”。
2. 安装官方 **FFmpeg** Windows 构建，确认 `ffmpeg.exe` 和 `ffprobe.exe` 都在同一 `bin` 目录。参考 [FFmpeg Downloads](https://ffmpeg.org/download.html)。
3. 在项目根目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 阻止激活脚本，无须调整系统策略，也可以直接用 `\.venv\Scripts\python.exe` 替代以下命令的 `python`。

有两种方式让程序找到 FFmpeg：

- 将 FFmpeg 的 `bin` 路径加入 Windows 用户 `PATH`，重开终端，运行 `ffmpeg -version` 与 `ffprobe -version` 验证；
- **不修改 PATH**：在 GUI「导出」页展开「高级：FFmpeg / FFprobe 路径」填写 `C:\ffmpeg\bin\ffmpeg.exe` 和 `C:\ffmpeg\bin\ffprobe.exe`；CLI 使用 `--ffmpeg`、`--ffprobe`。

## 启动 GUI

在项目根目录运行：

```powershell
python -m yingxu
```

也可以：

```powershell
python -m yingxu.gui
```

操作流程：**导入视频 → 设置版本数、种子、预设、导出目录 → 按需要打开画面 / 音频 / 水印 / 字幕蒙版 → 加入队列 → 开始处理**。运行中可暂停或取消；结束后用「重试失败」把失败项重新排队，再点「开始处理」。预览分原片静帧、处理后静帧，以及 3 秒效果片段。

GUI 任务日志同时写入用户主目录下的 `.yingxu/logs/年-月-日.log`。运行中不支持新加任务，须等待当前批次结束。

## 命令行示例

在仓库根目录执行；GUI 与 CLI **使用同一套真实处理引擎**。

### 单视频生成 5 个版本

```powershell
python -m yingxu.cli process --input "D:\素材\视频1.mp4" --output "D:\影序输出" --versions 5 --seed 2026 --preset "presets\标准处理.json" --report "D:\影序输出\report.json"
```

### 批量处理 30 条（或更多）视频

```powershell
python -m yingxu.cli process --input-dir "D:\素材" --output "D:\影序输出" --versions 2 --seed 77 --retries 2
```

带画面、水印和蒙版的方案保存成 JSON 后，用 `--profile` 交给同一套引擎：

```powershell
python -m yingxu.cli process --input "D:\素材\视频1.mp4" --output "D:\影序输出" --versions 3 --profile "D:\我的方案.json"
```

### 自定义 FFmpeg 路径、只做快速结构校验

```powershell
python -m yingxu.cli process --input "D:\素材\视频2.mp4" --output "D:\影序输出" --versions 1 --verify probe --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe" --ffprobe "C:\ffmpeg\bin\ffprobe.exe"
```

运行 `python -m yingxu.cli process --help` 查看全部选项。

> `--seed` 保证随机**参数**一致，但不保证跨 FFmpeg 版本/平台编码的文件字节完全一致。预设包含每个滤镜的最小值、最大值、H.264 CRF 和编码速度；范围在 `models.py` 中限制，避免异常值。

## 安全机制与限制

1. **原片不修改**：拒绝把输出目录设置为源视频的直接父目录。导出至独立目录；不执行源文件删除。
2. **不会覆盖现有结果**：相同版本文件已存在时，新增 `_001`、`_002` 等后缀。临时文件与最终文件在同一目录，优先使用原子硬链接创建最终文件；不支持硬链接的磁盘使用独占创建和复制（该模式若意外断电，可能留下不完整最终文件，建议使用 NTFS）。
3. **先验后交付**：FFprobe 检查视频轨道、原有音轨是否保留、时长误差是否在 `max(0.5秒, 2%)` 内；默认再运行 FFmpeg 完整解码校验。**校验不能代替人工观看画质和音画同步**。
4. **实时进度**：由 FFmpeg 的 `-progress` 输出计算，不是模拟值。结束阶段会执行较耗时的完整解码，因此 90% 以后可能较慢。
5. **暂停/取消**：通过 `psutil` 控制当前 FFmpeg 进程。取消时终止处理并清理临时文件；不要强制结束 Python 主程序，以免遗留临时文件。GUI 暂停作用于全队列，包含当前任务。
6. **音视频轨道限制**：只保留第一条视频轨。没有音频滤镜时尽量保留全部音轨；一旦调节音量、变速、均衡、降噪或背景音乐，只处理第一条音轨。不保留内嵌字幕、封面或附件。默认 H.264/AAC MP4，也可选 H.265。HDR、10-bit 或特殊色彩可能丢失，需要人工检查。
7. **输出分辨率**：尽量保持源分辨率，不自动镜像、抽帧或裁剪；H.264 yuv420p 对某些不规范的奇数分辨率可能拒绝编码，此时会报错并保留原片。
8. **安全边界**：无法保证处理后视频必然被第三方平台视为原创，也不等于取得素材版权。

## 测试

不需要 PySide6 即可运行引擎测试（需要 `psutil`）：

```powershell
python -m unittest discover -s tests -v
```

`test_integration.py` 在检测到 FFmpeg/FFprobe 后会生成 1.5 秒有声测试视频，验证**真实转码、完整校验、音轨和文件名冲突保护**；没有 FFmpeg 时自动跳过该测试。GUI 需要在安装 PySide6 的 Windows 环境打开后再做肉眼界面测试。

## 下一步扩展点

- 水印和蒙版的滤镜在 `yingxu/processing.py` 生成。`extensions.py` 在传入具体参数时返回同一套滤镜；不传参数仍会拒绝，避免空调用被当成已处理。
- 蒙版只覆盖用户框出的区域。人物、商品或运动背景上效果可能不好，这是覆盖，不是修复。
- 若后续要扩充滤镜，请在 `processing.py` 里集中构造，并保持未勾选的功能不会进入命令。
