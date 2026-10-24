# C++ 侧车实现方案（二期）

> 配套文档：[PRD.md](../PRD.md) 第 7.8 节 · [ADR-0003](./adr/0003-language-boundary-at-detector-seam.md)
> 状态：**待动手**。一期全 Python，本文件只在切换条件触发时才启用。

---

## 1. 边界：只做「像素进、框和 JPEG 出」

这一句是整个方案能不能收住的关键。C++ 侧不是「用 C++ 重写一遍系统」，而是一个**只负责把图像变成检测结果和带框图**的进程。判断某个功能要不要进 C++，问一句：**它需要逐像素访问吗？** 不需要就留 Python。

| 功能 | 归属 | 理由 |
|---|---|---|
| 相机采集 | **C++** | 逐像素，且需与推理同进程避免拷贝 |
| 预处理（letterbox / 归一化） | **C++** | 逐像素 |
| TensorRT 推理 | **C++** | 逐字节张量；放在 C++ 才不必安装 torch（**TRT 本身 Python 也能用，这不是上 C++ 的理由**，见 PRD 7.8） |
| 后处理（解码 / NMS / 坐标还原） | **C++** | 贴着推理输出，跨进程传原始张量毫无意义 |
| 画框 | **C++** | 逐像素 |
| JPEG 编码 | **C++** | 逐像素，且编码后体积小、利于跨进程传输 |
| **目标跟踪（ByteTrack）** | **Python** | ByteTrack 只吃 box 与 score、**不吃像素**，且 Python 侧有现成实现 |
| 停留判定 / 冷却 | Python | 纯时间逻辑，无像素 |
| TTS 合成与播放 | Python | HTTP 调用，Python 三行 |
| 告警存储 | Python | 文件 IO |
| Web 服务 / MJPEG / WebSocket | Python | 与像素无关 |
| 前端页面 | 不变 | 前端根本不知道后端换了语言 |

> **跟踪留在 Python 是这份方案最大的一处减负**：自己写 ByteTrack 的卡尔曼滤波 + 匈牙利匹配约 400~600 行，且调参靠经验、很容易出现 ID 频繁跳变。既然它不需要像素，就没有理由搬进 C++。

---

## 2. 文件与行数

```text
cpp/
├── CMakeLists.txt              60
├── src/
│   ├── main.cpp               120   参数解析、主循环、信号处理、阶段计时
│   ├── config.h/.cpp           70   yaml 读取与校验
│   ├── capture.h/.cpp         130   OpenCV 采集、格式校验、掉线重连
│   ├── preprocess.h/.cpp       90   letterbox、BGR→RGB、HWC→CHW、归一化
│   ├── engine.h/.cpp          220   TensorRT 加载、绑定、推理、stream
│   ├── postprocess.h/.cpp     180   解码、置信度过滤、NMS、坐标反映射
│   ├── draw.h/.cpp            150   框、标签、中文字体（FreeType）
│   ├── encode.h/.cpp           40   JPEG 编码
│   └── ipc.h/.cpp              90   Unix socket、信封封装、丢帧策略
├── assets/NotoSansCJKsc-Regular.otf   （约 10 MB，可裁剪子集）
└── models/yolov8n.engine              （由 Python 侧导出，不进版本库）
```

**合计约 1050 行 + 60 行 CMake。** 砍掉中文标签后约 900 行。这比早期估的 600~800 行更接近真实，主要差在字体渲染与掉线重连上。

---

## 3. 各模块要写的功能

### 3.1 capture —— 采集

| 函数 | 作用 |
|---|---|
| `bool open(const CameraCfg&, std::string& err)` | 打开设备、设 FOURCC=MJPG、设分辨率与帧率，**回读实际值**并与配置比对 |
| `bool read(cv::Mat& bgr)` | 取一帧；连续失败计数 |
| `void close()` | 释放 |
| `bool healthy() const` | 触发掉线重连的判定 |

要点：用 `cv::VideoCapture` 而不是裸写 V4L2。裸写能省一个依赖，但要多写 300 行 ioctl，且没有性能收益（真正的瓶颈在推理）。**回读实际分辨率**是因为相机经常静默降级到 640x480，不校验的话你会以为配置生效了。

### 3.2 preprocess —— 预处理

| 函数 | 作用 |
|---|---|
| `Letterbox letterbox(const cv::Mat& src, cv::Mat& dst, int size)` | 等比例缩放 + 灰边填充到 640×640，返回 `scale / pad_x / pad_y` |
| `void toBlob(const cv::Mat& rgb, std::vector<float>&)` | BGR→RGB、HWC→CHW、uint8→float、/255 |
| `void upload(const float*, float* d_ptr, cudaStream_t)` | 拷到显存 |

`Letterbox` 这三个数必须原样带到后处理，否则框会整体偏移——这是第一个容易漏的地方。

### 3.3 engine —— TensorRT

| 函数 | 作用 |
|---|---|
| `bool load(const std::string& path)` | 读序列化 engine、建 `IExecutionContext`、建 cudaStream |
| `void infer(const float* d_in, float* d_out)` | `enqueueV3`（或 `enqueueV2`）异步推理 + 同步 |
| `Dims outDims() const` | 输出维度，用于断言 `[1,84,8400]` |
| `std::string bindingInfo() const` | 启动时打印绑定名与维度，排查用 |

要点：启动时**打印绑定名与维度并断言**。engine 的输入输出名字不统一（`images`/`output0` 或 `input`/`output`），硬编码就会在换模型时静默失败。

### 3.4 postprocess —— 后处理（最高风险）

| 函数 | 作用 |
|---|---|
| `int argmaxClass(const float* out, int i, int num_classes, float& score)` | 取该框的最大类与分数 |
| `void decode(const float* out, int num_boxes, int num_classes, float conf, std::vector<Detection>&)` | 遍历 8400 个候选，过滤低分 |
| `void nms(std::vector<Detection>&, float iou_thres)` | 用 `cv::dnn::NMSBoxes`，不必手写 |
| `void unletterbox(std::vector<Detection>&, const Letterbox&, int w, int h)` | 反算回原图坐标并裁到边界内 |

**唯一必须记住的一件事**：YOLOv8 的导出输出是 **`[1, 84, 8400]`，通道优先**。第 `c` 个类在候选 `i` 上的分数是 `out[c * 8400 + i]`，**不是** `out[i * 84 + c]`。写反了不会报错，只会得到一堆位置荒唐的框——这是此类实现里最常见的 bug。

`cv::dnn::NMSBoxes` 要求 `cv::Rect2d`，且它按分数降序处理，直接可用，别自己写。

### 3.5 draw —— 画框与标签

| 函数 | 作用 |
|---|---|
| `void box(cv::Mat&, const Detection&, const cv::Scalar&)` | 矩形 + 类别配色 |
| `void label(cv::Mat&, const std::string& utf8, cv::Point, cv::Scalar)` | 中文文本，FreeType 渲染 |
| `bool loadFont(const std::string& ttf)` | 加载一次，全生命周期复用 |

**中文是两期共同的坑**：`cv::putText` 只支持 ASCII，写中文会变成一串 `???`。Python 一期的解法是 Pillow + 内嵌字体；C++ 二期用 FreeType 直接贴字（约 100 行），效果必须与 Python 版视觉一致。

若不想引 FreeType：标签降级为英文类名（`person` / `cup`），中文名只出现在网页的告警列表里。这是可接受的降级，但不是首选——画面上一半中文一半英文会很难看。

### 3.6 encode —— 编码

| 函数 | 作用 |
|---|---|
| `bool toJpeg(const cv::Mat&, std::vector<uchar>&, int quality)` | `cv::imencode(".jpg", ...)` |

质量默认 80。若编码耗时占比超过 15%，再考虑 turbojpeg（省 2~3 ms）。

### 3.7 ipc —— 传输

| 函数 | 作用 |
|---|---|
| `bool listen(const std::string& path)` | 建 Unix socket、等待连接（可阻塞） |
| `bool sendFrame(uint32_t frame_id, double ts, const std::vector<Detection>&, const std::vector<uchar>& jpeg)` | 组信封、发送 |
| `void setNonBlocking()` | 发送用非阻塞 |

**背压策略：丢帧，不排队。** Python 消费不过来时直接跳过本帧，绝不累积。一个实时视觉系统排队等消费，结果是延迟无限增长——比丢帧糟糕得多。

### 3.8 main —— 主循环

```text
读配置 → 校验 → 开相机 → 载 engine → 载字体 → listen
loop:
  read → letterbox → upload → infer → decode → nms → unletterbox
       → draw → encode → sendFrame
  每 30 帧打印: fps + pre/infer/post/draw/encode 各阶段耗时
SIGINT → 关闭相机 / 释放 engine / unlink socket
```

**阶段计时必须从第一天就有**。否则性能出问题时，你无法判断是推理慢还是编码慢，只能靠猜。

---

## 4. 接口修正：`tid` 不属于 C++

早期草稿把 `tid`（跟踪 ID）写进了检测框字段，这是错的——跟踪在 Python，C++ 根本不知道 ID。契约分两层：

```json
// C++ → Python（检测层，无 tid）
{"frame_id":1024,"ts":1761137465.123,"w":1280,"h":720,
 "boxes":[{"cls":0,"name":"人","conf":0.87,"xyxy":[312,180,489,640]}]}

// Python 内部（跟踪层，补上 tid）
{"cls":0,"name":"人","conf":0.87,"xyxy":[312,180,489,640],"tid":7}
```

`name`（中文类名）由 C++ 侧用内置映射表填，避免 Python 再维护一份。

---

## 5. 构建与依赖

| 依赖 | Jetson 上的来源 | 备注 |
|---|---|---|
| OpenCV | JetPack 自带 | 用系统版，别自己编 |
| TensorRT | JetPack 自带 | 头文件在 `/usr/include/aarch64-linux-gnu/`，库在 `/usr/lib/aarch64-linux-gnu/` |
| CUDA | JetPack 自带 | 只用到 `cudart` 与 stream |
| yaml-cpp | `apt install libyaml-cpp-dev` | |
| freetype | `apt install libfreetype-dev` | 中文标签用 |
| CMake | `apt install cmake` | ≥3.16 |

**TensorRT 没有 `find_package` 配置**，必须手写路径，别在这一步浪费时间。

**engine 与 TensorRT 版本强绑定**：`yolov8n.engine` 必须在**目标机**上由 ultralytics 导出，拷到别的机器上会 `deserialize` 失败。这是 TensorRT 的既有行为，不是 bug。

导出命令（在目标机执行，属于 Python 工具链）：

```bash
yolo export model=yolov8n.pt format=engine half=True imgsz=640
```

---

## 6. 三个高风险点与对策

| 风险 | 表现 | 对策 |
|---|---|---|
| **输出张量布局写反** | 框位置整体错乱、数量不对 | 先只跑 1 张图，把 top-1 框打印出来与 Python 版逐字段对比 |
| **engine 与运行时版本不匹配** | 启动即 `deserialize` 失败 | 只在目标机导出，把导出命令写进部署脚本 |
| **中文标签渲染** | 显示 `???` 或方块 | 先做英文标签跑通链路，再单独补 FreeType；字体缺字时降级英文 |

---

## 7. 工期

| 天 | 内容 | 当天必须验证 |
|---|---|---|
| D1 | CMake 骨架 + 采集 + JPEG + socket，**不推理，直接透传画面** | 浏览器能看到 C++ 送来的画面 |
| D2~D3 | 预处理 + engine 加载 + 推理（先不 NMS，打印原始输出维度） | 输出维度是 `[1,84,8400]`，数值量级合理 |
| D4~D5 | 解码 + NMS + 坐标反映射 | 模拟图上框与 Python 版重合 |
| D6 | 画框（英文）+ 阶段计时 | 各阶段耗时有数，知道瓶颈在哪 |
| D7 | 中文标签（FreeType） | 标签显示正常中文 |
| D8 | 配置、日志、优雅退出、掉线重连 | 拔插相机能自动恢复 |
| D9 | 联调与压测 | 见第 8 节验收 |

约 9 个工作日，含缓冲按 **2 周** 排。

---

## 8. 验收：跟 Python 版对齐，而不是「看起来对」

| 项 | 方法 | 通过线 |
|---|---|---|
| 检测一致性 | 100 张静态图（不同光照、不同人数），与 Python 版逐张比对 | 类别一致率 ≥99%，配对框 IoU ≥98% |
| 依赖收敛（主） | 目标机上 `pip list`，并断网重启 | **没有 torch**，程序照常运行 |
| 崩溃隔离（主） | 手动 `kill -9` 侧车进程 | Python 侧不崩，能重连或给出明确错误 |
| 性能（辅） | 同一台 Orin、同一相机、同一分辨率，比端到端 fps | **不低于** Python 版（不设倍数要求） |
| 稳定性 | 连续 2 小时 | 无崩溃、内存无增长、无 ID 跳变加剧 |

**止损条件**：判据必须和动机对齐。侧车是为了**甩掉 torch 依赖 + 崩溃隔离**，不是为了提速——推理速度在 Python 侧用 TensorRT 就能拿到（`.engine` + 官方 `tensorrt` 绑定），而 Python 侧用 TRT 也甩不掉 torch，因为 `ultralytics/nn/backends/tensorrt.py` 顶部就是 `import torch`。

因此：性能只需**不低于** Python 版。若修完边界后 fps 明显掉队（如 15fps 掉到 10fps 以下）且短期无法定位，说明 IPC 或编码引入了损失，**退回 `backend: python`**。耗时超过 2 周而「依赖 / 隔离 / 性能」三项未全绿，同样停手，改走「Python 版 + Docker 预装环境」这条更低成本的路。

---

## 9. Python 侧要写的适配器（约 100 行）

接缝在上层是**帧流**而非单帧：`LocalStream` 与 `SidecarStream` 产出同一个元组，上层 `for jpeg, boxes in stream` 两期不变。

```python
class LocalStream:
    """一期：相机与推理都在本进程，产出与侧车完全同构。"""
    def __iter__(self):
        while True:
            frame = self.camera.read()
            annotated, boxes = self.detector.process(frame)
            yield jpeg_encode(annotated), boxes


class SidecarStream:
    """二期：相机归 C++，这里只收成品。上层对这一行无感。"""
    def __init__(self, exe: str, config: str, sock: str):
        self.proc = subprocess.Popen([exe, "--config", config])
        self.sock = connect_with_retry(sock)     # 侧车启动需要几十毫秒

    def __iter__(self):
        while True:
            msg = self._recv()                   # 阻塞读一条信封
            yield msg.jpeg, msg.boxes

    def close(self):
        self.proc.terminate()
```

上层只按 `detector.backend` 决定用哪个类，其余代码不动。**这正是「预留窗口」的全部内容：一个产出元组形状固定的生成器。**

---

## 10. 这份方案刻意没做的事

- 不做多相机并发（需要每相机一个 engine 上下文，显存吃不消）。
- 不做 DeepStream 流水线（引入 GStreamer 全家桶，调试成本远大于收益）。
- 不做共享内存零拷贝（Unix socket 多一次拷贝约 1~2 ms，换来的复杂度不值）。
- 不做 C++ 侧跟踪（ByteTrack 不需要像素，见第 1 节）。
- 不做 C++ 侧 TTS 与 Web 服务（与像素无关）。

---

## 11. 如果二期同时要深度

**先明确一点：深度本身不是上 C++ 的理由。** `pyrealsense2` 与 `pyorbbecsdk2` 都有 Jetson 的 aarch64 预编译 wheel，深度、D2C 对齐、伪彩都能在 Python 里做。

但如果侧车已经接管了相机，Python 就打不开同一台设备（设备独占），深度只能由侧车带出来。此时需要增加：

| 改动 | 内容 | 增量 |
|---|---|---|
| 采集 | 换用 librealsense C++ API 或 Orbbec SDK C++ 取深度帧，并启用对齐 | +120 行 |
| 上色 | 深度图套 colormap，编码成第二张 JPEG | +30 行 |
| 协议 | JSON 增加 `depth_jpeg_len`，信封追加第二个 JPEG 块 | +20 行 |
| 配置 | 深度开关、深度分辨率、colormap、裁剪范围 | +30 行 |

合计 **+200~250 行、+2~3 天**。注意 GMSL 版 335Lg 还额外依赖解串板与 NVIDIA GMSL 驱动，这部分不在代码工作量里，属于硬件与驱动前置条件。

**信封扩展后**：

```text
[4B 总长 LE][4B JSON 长 LE][JSON][JPEG 彩色][JPEG 深度（depth_jpeg_len=0 时省略）]
```

```json
{"frame_id":1024,"ts":1761137465.123,"w":1280,"h":720,"depth_jpeg_len":18432,
 "boxes":[{"cls":0,"name":"人","conf":0.87,"xyxy":[312,180,489,640]}]}
```

两张图在同一封信里，天然同帧，不需要另做时间戳匹配。

**替代方案**：不上侧车，Python 全栈 + 厂商 SDK，深度与彩色同进程，对齐最省事。代价是保留 torch 依赖、性能上限低一些。取舍见 PRD 7.9。
