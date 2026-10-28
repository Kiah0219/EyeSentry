# EyeSentry

相机目标识别 · Web 推流 · 语音播报。

摄像头画面在边缘设备（NVIDIA Orin）上做实时目标检测，带检测框的画面推到浏览器；当有人在同一位置停留超过 5 秒，按页面选择的通道（本体 / 网页 / 静音）用中文语音提醒。

## 当前状态

**M2 已完成**：相机采集 → YOLO + ByteTrack → 画框（中文标签 + 每人已停留秒数）→ MJPEG → 浏览器；有人停留超过 5 秒时按当前播报通道（默认本体喇叭）中文播报，右侧出现带框截图的告警记录。

真机验收（A-03 帧率 / A-05 检测正确 / A-06 停留触发准确率）待在有相机的机器上完成。

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. 先看本机有哪些相机、哪个能出图
python tools/check_camera.py

# 2. 把建议的 source 填进 config.yaml，然后启动
python main.py
```

浏览器打开 `http://<本机IP>:8000`。

首次启动会自动下载 `models/yolov8n.pt`（约 6 MB）。网络不通时手动下载同名文件放进 `models/` 即可（GitHub 不通可用 `hf-mirror.com/Ultralytics/YOLOv8` 的同名文件）。默认只检测配置里白名单的 10 类（人 / 猫 / 背包 / 瓶子 / 杯子 / 椅子 / 笔记本 / 手机 / 书 / 时钟），改 `detector.classes` 增删，留空表示全部类别。页面顶部的「检测类别」也能直接勾选，下一帧生效、不用重启；置信度阈值同理（拖动滑块），勾掉全部类别等于回到全量检测。

页面顶部的「相机设备」可以直接切本机摄像头（UVC 设备或 HTTP MJPEG 源）：切换后画面自动重建，断流时页面每 1.5 秒自动重连，不用刷新；切换失败会保留原来的相机并提示原因。切换只在本次运行内生效，重启服务回到 `config.yaml` 里的 `camera.source`。

顶部的「目标检测」开关可以随时暂停推理：画面继续推流但不画框、不触发播报；恢复后跟踪 ID 与停留计时从头开始。`detector.backend: none` 时该开关置灰。

顶部的「播报」下拉三选一：本体（默认）/ 网页 / 静音。通道由服务端保存，同一时间只有一路出声，页面切换只影响下一次触发的播报；状态在本次运行内保持，重启回到 `config.yaml` 的 `tts.channel`。

### Jetson / Orin（CUDA）

Orin 上不能照「快速开始」直接 `pip install -r requirements.txt`：PyPI 的 aarch64 torchvision 是 CPU 构建，jetson-ai-lab 索引上的 torch 又是 CUDA 13 构建，与本机驱动不匹配。按 [docs/deploy-jetson.md](./docs/deploy-jetson.md) 的顺序装：NVIDIA v61 的 torch 2.5 → 补 cuSPARSE Lt 并软链 → 固定 numpy 1.26 → 源码编译 torchvision 0.20（sm_87）→ 其余依赖。实测 JetPack 6.2 / CUDA 12.6。

### 机器人 cockpit / Orbbec 相机

相机被 cockpit 独占时（Orbbec 335Lg 等），`/dev/video*` 打不开或只有红外图，也不要用 `ros2 topic echo`——cockpit 的帧只走 iceoryx 共享内存，普通用户和 rclpy 收不到。用 `tools/yirt2_mjpeg_bridge.py` 以 root 订阅 cockpit 的 ROS2 topic，再在本机暴露 MJPEG：

```bash
# 一次性安装 cockpit 自带的 Python 包（不进系统 Python）
.venv/bin/pip install --no-index --find-links /usr/share/cockpit-mid/wheels \
  yirt2 rmw_fastdds

# 需要 root：/tmp/roudi 与 iceoryx 共享内存是 root:root 0660
sudo .venv/bin/python tools/yirt2_mjpeg_bridge.py --port 8092
```

然后把 `config.yaml` 的 `camera.source` 改成 `http://127.0.0.1:8092/stream`、`fourcc` 留空。桥接默认订阅 `/cockpit/camera/chest/main/color`，可用 `--topic` 改；`/status` 看收发计数，`/snapshot.jpg` 抓单帧。它只依赖本机已装好的 cockpit middleware，不需要 Docker 或 systemd。

### 平台差异

| 平台 | `camera.source` | 相机授权 | 格式 / 帧率 | 设备喇叭 |
|---|---|---|---|---|
| macOS | 序号，如 `0` | 首次要在「系统设置 → 隐私与安全性 → 相机」允许所用的终端应用 | `fourcc` 被忽略，`fps` 由程序自行节流 | `afplay`（系统自带） |
| Linux / Orin | `/dev/videoN`，RealSense 会暴露多个节点；`check_camera.py` 用 MJPG 探测会误推红外节点，认准能出彩色画面的那个（见 [docs/deploy-jetson.md](./docs/deploy-jetson.md#8-选对相机节点)） | 无需 | 会设置 `fourcc` / `fps`，实际以设备上报为准，不符时启动日志有告警 | `ffplay`，需装 ffmpeg |
| Linux / cockpit | HTTP MJPEG，如 `http://127.0.0.1:8092/stream` | 桥接需 root（RouDi / 共享内存） | 桥接按 `--fps` 编码，默认 15；分辨率以 topic 为准 | 同 Linux |
| Windows | 序号，如 `0`（默认摄像头） | 首次要在「设置 → 隐私和安全性 → 相机」允许桌面应用访问 | 同 Linux | `ffplay`，需装 ffmpeg 并加入 PATH |

**播报通道选「网页」时，浏览器需要一次用户手势才允许出声**：页面在第一次点击或按键时会静默完成解锁（包括 Safari 对 `<audio>` 元素的限制），切到「网页」时也会再试一次；如果告警恰好在任何交互之前到达，页面会提示「网页播放需要先点一下页面」，不会静默失败。设备喇叭要求系统里有 `afplay`、`ffplay` 或 `gst-launch-1.0`（Jetson 通常有 GStreamer 而没有 ffmpeg），找不到播放器时本体通道只打一条日志；TTS 合成失败时才会退化成提示音。选「静音」或通道不通时告警仍会入库。

停留阈值、冷却时长、丢失容忍都在 `config.yaml` 的 `dwell` 段；播报通道在 `tts.channel`（`device` / `web` / `mute`），文案模板与音色也在 `tts` 段（`{n}` 人数、`{dwell}` 阈值秒数）。`tts.sink: auto` 跟随系统默认输出，也可以填 sink 名前缀固定；`tts.volume` 是应用侧音量，与系统音量相乘。多台机器可以各放一份配置，用 `python main.py -c config.<机器>.yaml` 启动。

接口：

- `GET /` 页面
- `GET /stream` MJPEG 流（带检测框）
- `GET /stream/raw` MJPEG 流（原始画面，不带框，只在有人看时编码）
- `GET /api/snapshot.jpg` 当前帧快照，加 `?raw=1` 拿不带框的原始帧
- `GET /api/status` 相机、帧流、检测器与通道状态（JSON）
- `GET /api/cameras` 本机可用的相机列表（含默认源与当前源）
- `POST /api/camera` 切换相机，如 `{"backend":"uvc","source":"1"}`；失败返回 409 并保留原相机
- `GET /api/state` 当前目标、已停留秒数、最近一条告警（JSON，给同机脚本轮询用）
- `GET /api/alerts?limit=50` 告警列表（倒序）
- `GET /api/alerts/{id}/snapshot.jpg` 告警截图
- `GET /audio/{id}.mp3` 播报音频
- `POST /api/settings` 修改白名单 / 阈值 / 检测开关 / 播报通道，如 `{"classes":[0,15],"conf":0.4,"detect":true,"channel":"web"}`；`channel` 只接受 `device` / `web` / `mute`，其他值返回 400
- `WS /ws/alerts` 实时推送告警

## 文档

| 文档 | 内容 |
|---|---|
| [PRD.md](./PRD.md) | 需求、用户故事、技术方案、验收标准、里程碑、风险 |
| [GLOSSARY.md](./GLOSSARY.md) | 领域术语（停留事件、帧包、播报通道等） |
| [docs/deploy-jetson.md](./docs/deploy-jetson.md) | Jetson Orin 依赖安装与启动（CUDA 版 torch / torchvision、相机节点、已知问题） |
| [docs/issues/](./docs/issues/) | 审计发现的问题台账（代码 bug / 风险） |
| [docs/cpp-sidecar-plan.md](./docs/cpp-sidecar-plan.md) | 二期 C++ 侧车的模块与函数清单 |
| [docs/adr/](./docs/adr/) | 架构决策记录 |

## 计划技术栈

- **一期（Python）**：OpenCV 采集 → Ultralytics YOLOv8n + ByteTrack → FastAPI 输出 MJPEG → edge-tts 播报（本体 / 网页 / 静音三选一）
- **前端**：单个 HTML + 原生 JS，无构建步骤
- **二期（可选）**：C++ 侧车接管采集与 TensorRT 推理，Python 侧仅做 Web、事件与播报；深度显示、GMSL、WebRTC

## 里程碑

| 阶段 | 内容 |
|---|---|
| M0 | 相机枚举、MJPEG 出画 |
| M1 | YOLO 检测、画框、中文标签 |
| M2 | ByteTrack 跟踪、停留判定、TTS 播报通道、告警列表 |
| M3 | Docker / systemd、文档、分享 |

## 设计要点

- 推流用 MJPEG：后端一个生成器，前端一个 `<img>`，无信令无编解码协商（[ADR-0002](./docs/adr/0002-mjpeg-now-webrtc-later.md)）
- 一期只用 UVC 彩色流，不接厂商 SDK（[ADR-0001](./docs/adr/0001-uvc-only-rgb-phase-one.md)）
- 上层只消费 `for jpeg, boxes in stream`，语言可换（[ADR-0003](./docs/adr/0003-language-boundary-at-detector-seam.md)）

## 硬件前提

RealSense D405 / D435、奥比中光 Gemini 335Lg、NVIDIA Orin。使用 USB3 数据线；335Lg 走 GMSL2 时需 Orin 侧解串板。
