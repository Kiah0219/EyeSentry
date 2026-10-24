# EyeSentry

相机目标识别 · Web 推流 · 语音播报。

摄像头画面在边缘设备（NVIDIA Orin）上做实时目标检测，带检测框的画面推到浏览器；当有人在同一位置停留超过 5 秒，浏览器与设备本体喇叭同时用中文语音提醒。

## 当前状态

**M2 已完成**：相机采集 → YOLO + ByteTrack → 画框（中文标签 + 每人已停留秒数）→ MJPEG → 浏览器；有人停留超过 5 秒，浏览器与本体喇叭同时中文播报，右侧出现带框截图的告警记录。

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

浏览器打开 `http://<本机IP>:8000`。Linux 上 RealSense 会暴露多个 `/dev/videoN`，认准 `check_camera.py` 里能出图的那个。

首次启动会自动下载 `models/yolov8n.pt`（约 6 MB）。网络不通时手动下载同名文件放进 `models/` 即可。默认只检测配置里白名单的 10 类（人 / 猫 / 背包 / 瓶子 / 杯子 / 椅子 / 笔记本 / 手机 / 书 / 时钟），改 `detector.classes` 增删，留空表示全部类别。页面顶部的「检测类别」也能直接勾选，下一帧生效、不用重启；置信度阈值同理（拖动滑块），勾掉全部类别等于回到全量检测。

macOS 首次运行需在「系统设置 → 隐私与安全性 → 相机」允许所用的终端应用；Linux 无需授权。另外 macOS 不支持指定像素格式与帧率，`config.yaml` 里的 `fourcc` 会被忽略、`fps` 由程序自行节流。

**播报需要先点页面左上角的「点击解锁音频」**——浏览器的自动播放策略要求一次用户手势，不解锁时页面会明确提示，不会静默失败。本体喇叭在 macOS 用 `afplay`、Linux 用 `ffplay`（需装 ffmpeg）。

停留阈值、冷却时长、丢失容忍都在 `config.yaml` 的 `dwell` 段；播报文案模板与音色在 `tts` 段（`{n}` 人数、`{dwell}` 阈值秒数）。

接口：

- `GET /` 页面
- `GET /stream` MJPEG 流
- `GET /api/status` 相机、帧流、检测器与通道状态（JSON）
- `GET /api/alerts?limit=50` 告警列表（倒序）
- `GET /api/alerts/{id}/snapshot.jpg` 告警截图
- `GET /audio/{id}.mp3` 播报音频
- `POST /api/settings` 修改白名单 / 阈值 / 通道开关，如 `{"classes":[0,15],"conf":0.4,"channels":{"web":true}}`
- `WS /ws/alerts` 实时推送告警

## 文档

| 文档 | 内容 |
|---|---|
| [PRD.md](./PRD.md) | 需求、用户故事、技术方案、验收标准、里程碑、风险 |
| [GLOSSARY.md](./GLOSSARY.md) | 领域术语（停留事件、帧包、TTS 通道等） |
| [docs/cpp-sidecar-plan.md](./docs/cpp-sidecar-plan.md) | 二期 C++ 侧车的模块与函数清单 |
| [docs/adr/](./docs/adr/) | 架构决策记录 |

## 计划技术栈

- **一期（Python）**：OpenCV 采集 → Ultralytics YOLOv8n + ByteTrack → FastAPI 输出 MJPEG → edge-tts 双通道播报
- **前端**：单个 HTML + 原生 JS，无构建步骤
- **二期（可选）**：C++ 侧车接管采集与 TensorRT 推理，Python 侧仅做 Web、事件与播报；深度显示、GMSL、WebRTC

## 里程碑

| 阶段 | 内容 |
|---|---|
| M0 | 相机枚举、MJPEG 出画 |
| M1 | YOLO 检测、画框、中文标签 |
| M2 | ByteTrack 跟踪、停留判定、TTS 双通道、告警列表 |
| M3 | Docker / systemd、文档、分享 |

## 设计要点

- 推流用 MJPEG：后端一个生成器，前端一个 `<img>`，无信令无编解码协商（[ADR-0002](./docs/adr/0002-mjpeg-now-webrtc-later.md)）
- 一期只用 UVC 彩色流，不接厂商 SDK（[ADR-0001](./docs/adr/0001-uvc-only-rgb-phase-one.md)）
- 上层只消费 `for jpeg, boxes in stream`，语言可换（[ADR-0003](./docs/adr/0003-language-boundary-at-detector-seam.md)）

## 硬件前提

RealSense D405 / D435、奥比中光 Gemini 335Lg、NVIDIA Orin。使用 USB3 数据线；335Lg 走 GMSL2 时需 Orin 侧解串板。
