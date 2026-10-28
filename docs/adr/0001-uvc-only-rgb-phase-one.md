# 一期只用 UVC 彩色流，不接厂商 SDK

一期把 RealSense、Mac/Windows 本地相机、奥比中光的彩色画面统一当成标准 UVC 视频节点处理，用 OpenCV 读取，不引入 pyrealsense2 与 Orbbec SDK。理由是 pyrealsense2 在 Jetson 上需要自行编译并配内核补丁，Orbbec SDK 与 GMSL 解串板绑定，任何一条卡住都会让「把画面推到浏览器」这个最小闭环迟迟不通；而 UVC 路径在三个平台上代码完全一致。代价是放弃深度、对齐与相机内参，这些在二期随深度显示一起补。

## Considered Options

- pyrealsense2：能拿深度与内参，但在 Jetson 上编译成本高，且一期用不到深度。
- Orbbec SDK：335Lg 的 GMSL 路径必需，但需要解串板与专有运行库，属于二期。

## 更新（2026-10-28）

ABO1 机器人的相机由 cockpit 托管，彩色与深度都以 yirt2 topic 提供（见 [deploy-log](../deploy-log.md) 的「相机帧是怎么拿到的」）。EyeSentry 一期用本地 MJPEG 桥接订阅，二期深度同样订阅 depth topic，**不再计划自研 Orbbec SDK / GMSL 接入**；RealSense 不在 cockpit 托管范围，二期的深度与对齐仍需要自行接 `pyrealsense2`。本 ADR 的 UVC 路径仍是 macOS、Windows、RealSense 和无 cockpit 场景的彩色做法。
