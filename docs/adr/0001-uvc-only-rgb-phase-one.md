# 一期只用 UVC 彩色流，不接厂商 SDK

一期把 RealSense、Mac/Windows 本地相机、奥比中光的彩色画面统一当成标准 UVC 视频节点处理，用 OpenCV 读取，不引入 pyrealsense2 与 Orbbec SDK。理由是 pyrealsense2 在 Jetson 上需要自行编译并配内核补丁，Orbbec SDK 与 GMSL 解串板绑定，任何一条卡住都会让「把画面推到浏览器」这个最小闭环迟迟不通；而 UVC 路径在三个平台上代码完全一致。代价是放弃深度、对齐与相机内参，这些在二期随深度显示一起补。

## Considered Options

- pyrealsense2：能拿深度与内参，但在 Jetson 上编译成本高，且一期用不到深度。
- Orbbec SDK：335Lg 的 GMSL 路径必需，但需要解串板与专有运行库，属于二期。
