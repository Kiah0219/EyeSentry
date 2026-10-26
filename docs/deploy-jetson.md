# Jetson Orin 部署（CUDA）

实测环境：Jetson Orin，JetPack 6.2 / L4T R36.4.3，Ubuntu 22.04.5，驱动 540.4.0，CUDA 12.6，cuDNN 9.3.0，Python 3.10.12，12 核，61 GiB 内存。日期 2026-10-26。

README 的「快速开始」只适用于 macOS / Windows 和 x86 Linux。Orin 上直接 `pip install -r requirements.txt` 会装错 torch，NMS 落到 CPU 或直接起不来，所以这里单独列一份顺序步骤。每一步的实测过程与走过的弯路见 [deploy-log.md](./deploy-log.md)。

网络前提：PyPI 直连较慢，下面统一用清华镜像；GitHub 与 huggingface.co 在本机不可达，torchvision 源码走 Gitee 镜像、模型走 hf-mirror。

## 1. 建 venv（不需要 sudo）

系统没有 `python3.10-venv`，`python3 -m venv` 会因 ensurepip 缺失而失败；`virtualenv` 装在用户目录即可绕过。

```bash
python3 -m pip install --user virtualenv
python3 -m virtualenv .venv
source .venv/bin/activate
```

## 2. 安装 CUDA 版 PyTorch

不要用 PyPI 或 jetson-ai-lab 的默认版本：

- `pypi.jetson-ai-lab.io/jp6/cu126` 上的 torch 2.11 依赖 `cuda-toolkit==13.0.2`，而驱动 540 只支持到 CUDA 12.6；
- PyPI 的 torchvision aarch64 轮子是 CPU 构建（`_C.so` 只链接 `libtorch_cpu`），GPU NMS 不可用。

用 NVIDIA 官方 v61 目录的 torch（806 MB）：

```bash
pip install "https://developer.download.nvidia.cn/compute/redist/jp/v61/pytorch/torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl"
```

参考 sha256：`6f75fd2d2ef840ede1a90dbcf40a5458214bee26cc803fa510cda2e8978d972a`。`developer.download.nvidia.com` 会 301 到 `.cn`，用 `.cn` 直链可少一次跳转。

## 3. 补 cuSPARSE Lt 并软链

JetPack 6.2 上 torch 2.5 导入会报 `libcusparseLt.so.0: cannot open shared object file`。

**为什么需要**：`libtorch_cuda.so` 的 `NEEDED` 里有 `libcusparseLt.so.0`（torch 的稀疏算子要用），但 JetPack 6.2 系统不提供这个库，NVIDIA 的 torch 轮子也没打包它、元数据里也没有声明。PyPI 的 `nvidia-cusparselt-cu12` 提供同 SONAME 的实现（0.7.1，下载约 284 MB，安装后 421 MB）。

**为什么用软链而不是 `LD_LIBRARY_PATH`**：`libtorch_cuda.so` 的 RUNPATH 是 `$ORIGIN:/usr/local/cuda/lib64/stubs:/usr/local/cuda/lib64`，`$ORIGIN` 就是 `torch/lib`；而 pip 把库装在 `site-packages/nvidia/cusparselt/lib/`，不在任何搜索路径里。链进 `torch/lib` 就能被找到，且不必每次启动都设环境变量（nohup / systemd 都省事），也不重复占 421 MB。

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple nvidia-cusparselt-cu12==0.7.1
ln -s ../../nvidia/cusparselt/lib/libcusparseLt.so.0 \
  .venv/lib/python3.10/site-packages/torch/lib/libcusparseLt.so.0
```

验证：

```bash
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
ldd .venv/lib/python3.10/site-packages/torch/lib/libtorch_cuda.so | grep -i cusparselt
```

应输出 `2.5.0a0+872d972e41.nv24.8 True Orin`（轮子文件名是 `nv24.08`，pip 规范化后显示 `nv24.8`），`ldd` 那行解析到 `torch/lib/libcusparseLt.so.0`。

撤销（撤销后 `import torch` 会重新报错，服务起不来；全部在 venv 内，不涉及系统目录）：

```bash
rm .venv/lib/python3.10/site-packages/torch/lib/libcusparseLt.so.0
.venv/bin/pip uninstall -y nvidia-cusparselt-cu12
```

## 4. 固定 NumPy 1.26

torch 2.5 是 NumPy 1.x ABI 构建，装到 numpy 2.x 会在导入或张量互操作时出错：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "numpy==1.26.4"
```

## 5. 源码编译 CUDA 版 torchvision

PyPI 只有 CPU 轮子，NVIDIA v61 目录没有 torchvision，GitHub 又不可达。从 Gitee 镜像取 0.20.0 源码，按 Orin 的算力 8.7 编译：

```bash
curl -L -o /tmp/vision-0.20.0.tar.gz https://gitee.com/mirrors/vision/repository/archive/v0.20.0.tar.gz
mkdir -p /tmp/tv_build && tar -xzf /tmp/vision-0.20.0.tar.gz -C /tmp/tv_build
cd /tmp/tv_build/vision-v0.20.0

FORCE_CUDA=1 TORCH_CUDA_ARCH_LIST=8.7 MAX_JOBS=10 \
USE_FFMPEG=0 USE_VIDEO_CODEC=0 USE_NVJPEG=0 USE_NVCODEC=0 \
pip install . --no-build-isolation
```

`--no-build-isolation` 让编译用 venv 里已装好的 torch；本机没有 ffmpeg，所以关掉相关扩展。12 核大约 12 分钟。

验证 GPU NMS：

```bash
python - <<'PY'
import torch
from torchvision.ops import nms
boxes = torch.tensor([[0, 0, 10, 10], [1, 1, 11, 11]], device="cuda")
scores = torch.tensor([0.9, 0.8], device="cuda")
print(nms(boxes, scores, 0.5))
PY
```

应输出 `tensor([0], device='cuda:0')`。如果输出在 CPU 上或报 `libtorch_cuda` 缺失，说明装成了 CPU 轮子，重做本步。

## 6. 安装其余依赖

显式列包，`ultralytics` 用 `--no-deps` 安装，避免它改写已经装好的 torch / numpy / OpenCV：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  matplotlib pillow PyYAML requests psutil polars nvidia-ml-py ultralytics-thop \
  "opencv-python==4.11.0.86" fastapi "uvicorn[standard]" edge-tts lap
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --no-deps "ultralytics==8.4.189"
```

OpenCV 固定 4.11：更新的大版本与当前画框 / 编码路径未经验证。装完可用 `pip check` 看有没有被顶掉的依赖。

## 7. 准备模型

服务首次启动会自己从 GitHub 下载 `yolov8n.pt`；GitHub 不通时手动放一份到 `models/`：

```bash
curl -L -o models/yolov8n.pt https://hf-mirror.com/Ultralytics/YOLOv8/resolve/main/yolov8n.pt
```

应为 6,534,387 字节。

## 8. 选对相机节点

`tools/check_camera.py` 用强制 MJPG 的方式探测，在 RealSense 上会给出误导结论：这些节点不支持 MJPG，强制设置只会得到纯绿帧。按设备默认格式逐节点抓一帧再判断：

- `/dev/video4`：彩色 YUYV，正常画面；
- `/dev/video2`：UYVY 红外灰度，过曝发白；
- `video0/1/3/5`：打不开（深度等用途）。

`config.yaml` 用彩色节点，且不要强制 MJPG：

```yaml
camera:
  source: /dev/video4
  width: 1280
  height: 720
  fps: 15
  fourcc: YUYV
```

## 9. 启动与自检

```bash
setsid nohup .venv/bin/python main.py >> /tmp/eyesentry.log 2>&1 &
curl -s http://127.0.0.1:8000/api/status
```

看三个数：`camera.source` 是否为预期节点、`stream.fps` 是否接近 15、`stream.last_frame_age_s` 是否在 1 秒内。页面打开 `http://<本机IP>:8000`。

## 10. 已知问题

- **本体喇叭不出声**：Orin 上没有 `ffplay`，本体通道只打一条日志，网页通道不受影响（需在页面上先点一下解锁音频）。装播放器：`sudo apt install -y ffmpeg`。
- **停服务卡住**：有浏览器挂着 `/stream` 时，SIGTERM 会停在「Waiting for background tasks to complete」，只能 `kill -9`。待给 `uvicorn.run` 加 `timeout_graceful_shutdown=5`。
- **不会开机自启**：目前是手动后台进程，重启机器就没了。systemd 单元待补。
- **构建目录**：`/tmp/tv_build` 约 52 MB，装完可删。

## 附：踩坑速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `python3 -m venv` 报 ensurepip 不可用 | 缺 `python3.10-venv`，装它要 sudo | 用 `virtualenv` 建 venv（步骤 1） |
| 装 torch 时被要求 `cuda-toolkit==13.0.2` | jp6/cu126 索引上已是 CUDA 13 构建 | 改用 NVIDIA v61 轮子（步骤 2） |
| `libcusparseLt.so.0` 找不到 | JetPack 6.2 未自带该库 | 装 `nvidia-cusparselt-cu12` 并软链（步骤 3） |
| 导入 torch 报 NumPy ABI 错误 | numpy 2.x 与 torch 2.5 不兼容 | 固定 numpy 1.26.4（步骤 4） |
| 相机强制 MJPG 出纯绿帧 | RealSense 节点不支持 MJPG | 用默认格式，`fourcc: YUYV`（步骤 8） |
| `POST /api/camera` 返回 500，`'Event' object is not callable` | 已修复：`LocalStream._stop` 覆盖了 `Thread._stop()` | 见 `stream.py` 的 `_stop_event` |
