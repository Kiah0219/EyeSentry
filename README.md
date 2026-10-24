# EyeSentry

相机目标识别 · Web 推流 · 语音播报。

摄像头画面在边缘设备（NVIDIA Orin）上做实时目标检测，带检测框的画面推到浏览器；当有人在同一位置停留超过 5 秒，浏览器与设备本体喇叭同时用中文语音提醒。

## 当前状态

**M0 已完成**：相机采集 → MJPEG → 浏览器出画，页面顶部显示相机源、分辨率、实测帧率。
接下来 M1 接入 YOLO 检测与画框。

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

- `GET /` 页面
- `GET /stream` MJPEG 流
- `GET /status` 相机与帧流状态（JSON）

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
| M2 | 停留判定、TTS 双通道、告警列表 |
| M3 | Docker / systemd、文档、分享 |

## 设计要点

- 推流用 MJPEG：后端一个生成器，前端一个 `<img>`，无信令无编解码协商（[ADR-0002](./docs/adr/0002-mjpeg-now-webrtc-later.md)）
- 一期只用 UVC 彩色流，不接厂商 SDK（[ADR-0001](./docs/adr/0001-uvc-only-rgb-phase-one.md)）
- 上层只消费 `for jpeg, boxes in stream`，语言可换（[ADR-0003](./docs/adr/0003-language-boundary-at-detector-seam.md)）

## 硬件前提

RealSense D405 / D435、奥比中光 Gemini 335Lg、NVIDIA Orin。使用 USB3 数据线；335Lg 走 GMSL2 时需 Orin 侧解串板。
