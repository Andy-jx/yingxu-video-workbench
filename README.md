# 影序 · 智能视频处理工作台（V0）

基于 **Python + PySide6 + FFmpeg / FFprobe** 的本地 Windows 视频批量处理工作台。提供可运行的中文 GUI 和共用的命令行处理引擎，不依赖云端 API。代码为本项目独立实现，未搬运第三方仓库。

> **范围声明**：V0 实现安全的视频批量转码与轻量参数变化，不保证任何平台将输出视频认定为“原创”或通过重复内容审核。处理他人素材前请确认有权使用。动态水印和字幕遮罩目前只是明确的扩展接口，**尚未实现**。

## 当前已实现

- 单条视频生成多个版本，固定种子可重复生成相同**参数**；不同版本使用独立种子。
- 多视频任务队列、每个任务独立进度、暂停/继续（包括当前 FFmpeg 子进程）、取消、自动重试，GUI 可将失败/取消项重新入队。
- FFmpeg 真正编码：柔和的亮度、对比度、饱和度、音频增益随机范围（由预设控制），H.264 / AAC 输出 MP4。
- 输出目录与源目录分开，原片从不覆盖；FFmpeg 先写临时文件，通过 FFprobe 验证视频/音频轨道和时长；默认再完整解码校验；最后以不覆盖已有文件的方式发布。
- 预设 JSON、CLI JSON 报告、中文日志和 GUI 环境检查；为动态水印和字幕蒙版留出 Python 接口。

**并未实现：** 智能镜头重组、局部字幕去除、动态水印、任意裁剪、镜像、抽帧、变速、GPU 编码、安装包。不存在相应的“假功能按钮”。

## 目录

```text
yingxu-video-workbench/
├── yingxu/
│   ├── __init__.py
│   ├── __main__.py         # python -m yingxu（GUI）/ process（CLI）
│   ├── gui.py              # PySide6 中文窗口
│   ├── cli.py              # 命令行
│   ├── queue.py            # 共用任务队列：进度、暂停、取消、重试
│   ├── engine.py           # FFmpeg 调用与导出校验
│   ├── models.py           # 任务和预设定义
│   └── extensions.py       # 动态水印 / 字幕遮罩扩展接口（未实现）
├── presets/
│   ├── 只转码.json
│   ├── 轻度处理.json
│   └── 标准处理.json
├── tests/
│   ├── test_core.py
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
- **不修改 PATH**：在 GUI 右侧两个输入框分别填写 `C:\ffmpeg\bin\ffmpeg.exe` 和 `C:\ffmpeg\bin\ffprobe.exe`（替换为你的实际路径）；CLI 使用 `--ffmpeg`、`--ffprobe` 参数指定。

## 启动 GUI

在项目根目录运行：

```powershell
python -m yingxu
```

也可以：

```powershell
python -m yingxu.gui
```

操作流程：**导入视频 → 设置每条视频版本数、随机种子、预设、输出目录 → 加入批量队列 → 检查 FFmpeg 环境 → 开始处理**。运行中可暂停/继续整个队列，选中任务取消；处理结束后选中失败任务点击“重试失败/取消项”，再点击“开始处理”。

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
6. **音视频轨道限制**：V0 只输出**第一个视频轨道及所有音轨**，不保留额外视频轨道、内嵌字幕、封面或附件；不允许将该版本用于必须保留这些轨道的文件。统一转换为 H.264/AAC MP4，输入 HDR、10-bit 或特殊色彩的视频可能失去对应特性；需人工检查。
7. **输出分辨率**：尽量保持源分辨率，不自动镜像、抽帧或裁剪；H.264 yuv420p 对某些不规范的奇数分辨率可能拒绝编码，此时会报错并保留原片。
8. **安全边界**：无法保证处理后视频必然被第三方平台视为原创，也不等于取得素材版权。

## 测试

不需要 PySide6 即可运行引擎测试（需要 `psutil`）：

```powershell
python -m unittest discover -s tests -v
```

`test_integration.py` 在检测到 FFmpeg/FFprobe 后会生成 1.5 秒有声测试视频，验证**真实转码、完整校验、音轨和文件名冲突保护**；没有 FFmpeg 时自动跳过该测试。GUI 需要在安装 PySide6 的 Windows 环境打开后再做肉眼界面测试。

## 下一步扩展点

- `yingxu/extensions.py` 中的 `DynamicWatermarkExtension.build_filters(...)` 预留未来动态水印滤镜生成接口；V0 主引擎尚未调用它。
- `SubtitleMaskExtension.build_filters(...)` 为未来按时间段 / XY 坐标的字幕蒙版预留；复杂背景的局部覆盖需要单独实测，不宣称“无痕”。
- 若后续要扩充滤镜，请在 `engine.py` 的 `_make_filters` 中集中构造且保持参数白名单，避免 GUI 直接拼接任意命令。
