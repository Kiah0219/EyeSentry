# C++ 与 Python 的边界画在检测接缝上，不混编

要求「C++ 与 Python 并存」，落地方式取进程隔离而非 pybind11 同进程混编：检测后的输出被收敛成一个语言无关的字段契约（`cls / name / conf / xyxy / tid`），一期由进程内的 `PyDetector` 实现，二期由独立进程的 `CppDetector` 实现同一契约。这样既能拿到 C++ 编译化带来的部署收益（目标机不必安装 PyTorch 与 ultralytics），又保住 Python 在事件逻辑、TTS、页面上的迭代速度。

## Considered Options

- pybind11 扩展：只加速推理，不解决目标机的重依赖问题，且构建与调试复杂度上升。
- 全 C++ 单二进制：部署最干净，但需自己重写 WebSocket 服务、TTS 调用与音频播放，工期从约 2.5 周涨到 4~6 周，且 YOLO 后处理是高风险点。

## Consequences

- 启用侧车后，相机与画框归 C++ 进程所有，Python 不再读取 `/dev/videoX`。
- 切换是配置项级别的，但切换前必须用同一张静态图对齐两边的框坐标。
