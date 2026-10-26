# 部署操作记录

- 目标机器：Jetson Orin（Ubuntu 22.04.5，JetPack 6.2 / L4T R36.4.3，aarch64）
- 部署方式：按 README「快速开始」在项目目录建 venv 安装依赖，探测相机后启动 FastAPI 服务
- 记录约定：所有操作（含只读检查）按时间顺序追加，命令原样记录，结果只写关键结论
- 用户要求（2026-10-26 12:27）：本次部署的每一步都要记录

## 2026-10-26

| 时间 | 操作 | 结果 |
|---|---|---|
| 12:26 | `ls -la` 项目根目录、`**/*.md` | Python 单体项目（FastAPI + OpenCV + YOLO），无 Dockerfile / systemd 单元 |
| 12:26 | 读 `README.md`、`requirements.txt`、`config.yaml` | 部署路径：venv → `pip install -r requirements.txt` → `tools/check_camera.py` → `python main.py`；服务端口 8000；模型首次启动自动下载 |
| 12:26 | 环境探测：`python3 --version`、`which`、`ls /dev/video*`、`free -h`、`nvidia-smi`、`/etc/os-release` | Python 3.10.12；Orin GPU（驱动 540.4.0 / CUDA 12.6）；存在 `/dev/video0`–`/dev/video5`；内存 61 GiB；ffmpeg / afplay 未安装；无 `.venv` |
| 12:26 | 读 `tools/check_camera.py`、`main.py`（前 80 行） | 相机探测工具用 MJPG 逐个打开节点验证出图；服务入口 `python main.py`，配置在 `config.yaml` |
| 12:26 | 导入检查 `cv2, fastapi, yaml, ultralytics, edge_tts` | 系统仅自带 cv2 4.5.4；fastapi / ultralytics / edge-tts 均缺失；未安装 torch |
| 12:26 | `curl -sI https://pypi.org/simple/` | HTTP/2 200，PyPI 可达 |
| 12:27 | 查询 PyPI 各包 aarch64 轮子 | opencv-python / torch / torchvision 均有 aarch64 轮子；ultralytics 为纯 Python 包 |
| 12:27 | `curl -sI https://pypi.jetson-ai-lab.dev/jp6/cu126/`、`developer.download.nvidia.com/.../v61/pytorch/` | 前者已下线（410）；后者 301 跳转到 nvidia.cn |
| 12:27 | `curl -sIL https://pypi.jetson-ai-lab.io/jp6/cu126/` | 可达（200），可作为 Jetson CUDA 版 torch 源 |
| 12:27 | `sudo -n true`、`which v4l2-ctl` | sudo 需要密码；v4l2-ctl 未安装（ffmpeg 安装与 systemd 开机自启需用户授权） |
| 12:27 | `df -h /home/data`、`python3 -c "import numpy"` | 可用 300 GiB；系统 numpy 1.21.5 |
| 12:27 | 查询 opencv-python 4.x aarch64 轮子 | 4.14.0.94 / 4.13 / 4.12 / 4.11 均有 aarch64 轮子（用于避免直接升到 OpenCV 5.0） |

| 12:28 | `python3 tools/check_camera.py`（系统 OpenCV 4.5.4） | `/dev/video2`（UYVY）与 `/dev/video4`（YUYV）可出图，工具建议 video2 |
| 12:28 | `python3 -m venv .venv` | 失败：系统缺 `python3.10-venv`（ensurepip 不可用），安装需 sudo |
| 12:28 | `python3 -m pip install --user virtualenv`、`rm -rf .venv`、`python3 -m virtualenv .venv` | 免 sudo 建好 `.venv`，内置 pip 26.2.2 / setuptools 84.2.1 |
| 12:28 | 后台：`pip install --index-url https://pypi.jetson-ai-lab.io/jp6/cu126 --extra-index-url https://pypi.org/simple torch==2.11.0 torchvision==0.26.0`，随后 `pip install opencv-python==4.11.0.86 -r requirements.txt` | 进行中（见下条结果） |
| 12:28 | 读 `camera.py`；`cat /sys/class/video4linux/video*/name` | 6 个节点同属 Intel RealSense；相机打开逻辑会按配置的 fourcc / 分辨率设置，并在不符时告警 |
| 12:29 | 强制 MJPG 抓 `/dev/video2`、`/dev/video4` 各一帧 | 两帧均为纯绿无内容（RealSense 这些节点不支持 MJPG，强制设置会破坏格式） |
| 12:29 | 不指定格式，逐一抓 `/dev/video0`–`/dev/video5` | video0/1/3/5 打不开；video2 = UYVY 灰度（红外，过曝）；video4 = YUYV 彩色，画面正常 |
| 12:29 | 修改 `config.yaml` | `camera.source: /dev/video4`，`fourcc: YUYV`，分辨率 1280x720@15 保持不变 |

| 12:30 | 检查后台 pip 输出 | torch 2.11.0 依赖 `cuda-toolkit==13.0.2`（CUDA 13），与本机驱动 540.4.0 / CUDA 12.6 不匹配，`TaskStop` 终止安装 |
| 12:30 | 查询 NVIDIA `jp/v62`、`jp/v61` 官方目录 | v62 不存在；v61 提供 `torch-2.5.0a0+872d972e41.nv24.08.17622132-cp310-cp310-linux_aarch64.whl`（806 MB，适配 CUDA 12.6） |
| 12:31 | 检查 `pypi.jetson-ai-lab.io/jp6/cu126` 的 torch / torchvision 列表 | torch 仅有 2.8.0+（均为 CUDA 13 通用构建）；torchvision 0.23–0.26，无 0.20 |
| 12:31 | 探测其他 Jetson 索引路径（jp61、cu122、jp6 等） | 全部 302 重定向，无可用旧版本 |
| 12:32 | `curl -L` 下载 GitHub ultralytics 资产的 torchvision 0.20.0 轮子 | 40 秒超时，GitHub 不可达；官方 v61 torchvision 目录同样无文件（404） |
| 12:33 | 检查编译工具链 | `/usr/local/cuda-12.6/bin/nvcc` 12.6.68、g++ 11.4、libjpeg/libpng 开发包齐全；驱动 540.4.0 |
| 12:33 | 查询 PyPI `torchvision==0.20.0` | 有 cp310 manylinux2014 aarch64 轮子（19.9 MB，需验证是否含 CUDA NMS） |
| 12:34 | 下载并解包 ultralytics 源码，检查 NMS 实现 | 进行中（见下条结果） |
| 12:35 | 开始安装 `torch-2.5.0a0+872d972e41.nv24.08`（NVIDIA v61 直链） | 后台执行（807 MB） |

| 12:36 | 安装 NVIDIA v61 torch 2.5.0 完成；`import torch` | 失败：`libcusparseLt.so.0: cannot open shared object file`（JetPack 6.2 已知兼容问题） |
| 12:36 | 检查系统 `libcusparseLt`、cuDNN | 系统无 libcusparseLt；cuDNN 9 正常；PyPI `nvidia-cusparselt-cu12` 有 aarch64 0.7.1 轮子（283.8 MB） |
| 12:37 | 检查 PyPI `torchvision==0.20.0` aarch64 轮子 | `_C.so` 仅链接 `libtorch_cpu`，无 `libtorch_cuda`，为 CPU 构建，GPU NMS 不可用 |
| 12:37 | 检查 ultralytics 8.4.189 源码 | 自带纯 Torch NMS（`utils/nms.py: TorchNMS`），但 `autobackend.warmup` 会无条件 `import torchvision`，因此仍需可用的 torchvision |
| 12:38 | 决定：安装 cuSPARSE Lt 0.7.1；torchvision 从 PyPI sdist 源码编译 CUDA 版（nvcc 12.6，`TORCH_CUDA_ARCH_LIST=8.7`） | 进行中 |

| 12:39 | 后台安装 `nvidia-cusparselt-cu12==0.7.1`（aarch64，283.8 MB） | 下载中（同时提供 torch 需要的 `libcusparseLt.so.0`） |
| 12:40 | 查询 PyPI torchvision 0.20.0 源码包 | PyPI 无 sdist，只有 wheel |
| 12:41 | 测试源码镜像：Gitee / codeload / raw.githubusercontent | Gitee 镜像 `gitee.com/mirrors/vision` 可用；GitHub 域名超时 |
| 12:42 | 从 Gitee 下载 `v0.20.0.tar.gz`（13 MB）并解包 | 版本 `0.20.0a0`，与 0.20.0 发布一致；CUDA 构建由 `FORCE_CUDA=1` 开启 |
| 12:43 | 检查构建条件 | 12 核；`/usr/include/python3.10/Python.h` 存在；g++ / nvcc / libjpeg / libpng 齐全 |
| 12:44 | 从 `hf-mirror.com` 下载 `yolov8n.pt` 到 `models/`（GitHub 与 huggingface.co 不可达） | 6.3 MB，文件完整（zip 格式） |
| 12:45 | 等待 cuSPARSE Lt 下载完成 | 进行中 |

| 12:45 | 观察 pip 下载：缓存 807 MB 未增长，下载停滞 | 终止后台安装 |
| 12:48 | 测试清华 / 阿里 PyPI 镜像 | 均 200；清华镜像 2 MB 测速约 2.5 MB/s |
| 12:51 | 从清华镜像断点续传 `nvidia-cusparselt-cu12==0.7.1` 轮子 | 283,835,557 字节；sha256 `8878dce7…` 与索引一致 |
| 12:52 | 安装 cuSPARSE Lt；带 `LD_LIBRARY_PATH` 导入 torch | torch 2.5.0a0+872d972e41.nv24.08；CUDA 12.6 可用；设备 Orin |
| 12:52 | 发现 numpy 2.2.6 与 torch 2.5（NumPy 1.x ABI）不兼容 | 降级到 numpy 1.26.4，torch ↔ numpy 互操作正常 |
| 12:52 | 在 `torch/lib` 建立 `libcusparseLt.so.0` 软链（RUNPATH 含 `$ORIGIN`） | 不设 `LD_LIBRARY_PATH` 也能导入 torch；cuDNN 9.3.0 |
| 12:53 | 启动 torchvision 0.20.0 CUDA 源码编译（`FORCE_CUDA=1`、`TORCH_CUDA_ARCH_LIST=8.7`、关闭 ffmpeg/nvcodec/nvjpeg、`MAX_JOBS=10`） | 后台进行中 |

| 13:05 | 后台编译 torchvision 0.20.0 CUDA 版完成（exit 0）；GPU 上测试 `torchvision.ops.nms` | 返回 keep index `[0]`，CUDA NMS 正常 |
| 13:06 | 并行安装其余依赖：matplotlib / pillow / pyyaml / requests / psutil / polars / nvidia-ml-py / ultralytics-thop / opencv-python 4.11.0.86 / fastapi / uvicorn / edge-tts / lap，ultralytics 8.4.189（`--no-deps`） | 成功；OpenCV 4.11.0、FastAPI 0.162.0、edge-tts 7.3.1、numpy 1.26.4 |
| 13:07 | 用相机帧实测 YOLO + ByteTrack | 模型加载 0.06s；首次推理 2.46s（预热），之后约 27ms/帧（≈37 FPS），设备 `cuda:0`，检出 1 个目标 |
| 13:08 | 检查中文字体与 8000 端口 | `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` 存在（中文标签可用）；端口空闲 |
| 13:09 | `setsid nohup .venv/bin/python main.py >> /tmp/eyesentry.log 2>&1 &` | 相机 `/dev/video4` 1280x720@15 YUYV 打开成功；检测器就绪；uvicorn 监听 `0.0.0.0:8000`；警告找不到 ffplay（本体通道只打日志） |
| 13:09 | 验证 `GET /`、`/api/status`、`/api/snapshot.jpg`、`/stream` | 全部 200；流 14.9 FPS，画面含 1 个检测框与中文标签「人 0.65 · 14.1s」；MJPEG 头正常 |
| 13:09 | 验证停留告警链路 | 1 人停留 5.0s 触发告警；`data/snapshots/*.jpg` 与 edge-tts 合成的 18 KB MP3 均生成，告警 JSONL 已写入；本体播放因缺 ffplay 跳过 |
| 13:10 | 待用户授权 | 安装 ffmpeg（`apt`，需 sudo 密码）与 systemd 开机自启（需 sudo）；当前服务为手动后台进程，重启后不会自动拉起 |

| 13:13 | 用户反馈切换相机后黑屏、切回无信号；检查 `/tmp/eyesentry.log` | `POST /api/camera` 返回 500：`TypeError: 'Event' object is not callable`，堆栈为 `Runtime.switch_camera → old_stream.join()` |
| 13:14 | 定位根因 | `LocalStream` 用 `self._stop` 保存停止事件，覆盖了 `threading.Thread._stop()` 内部方法；`join()` 调它时崩溃。旧流已停止且未重建，画面因此黑屏/无信号 |
| 13:14 | 修改 `stream.py` | 停止事件重命名为 `_stop_event`（4 处），切换流程本身不改 |
| 13:15 | 重启服务 | 旧进程 SIGTERM 后卡在「Waiting for background tasks」（浏览器 MJPEG 长连接阻塞优雅退出），改用 `kill -9`；新进程 PID 45933，`/dev/video4` 恢复 14.4–15 FPS |
| 13:16 | 回归：`POST /api/camera` 切到 `/dev/video2`，再切回 `/dev/video4` | 两次均 HTTP 200；切换后流转发正常（15 FPS、帧持续更新），日志无异常；截图确认画面恢复 |
| 13:16 | 遗留风险（未改代码） | 有 `/stream` 客户端时 SIGTERM 会挂起，重启需 `kill -9`；可加 `uvicorn.run(..., timeout_graceful_shutdown=5)` 修复（待确认） |

| 13:18 | 修复后复查服务状态 | PID 45933 运行约 1 分钟；`/api/status` 返回 `/dev/video4` 1280x720@15、`stream.fps=15.0`、`last_frame_age_s=0.06`、1 个检测框；日志中告警链路持续正常（此前误记为 1h15m，实为 `etime` 的 `mm:ss`） |
| 13:33 | 用户要求停止进程；`kill -TERM 45933` | 10s 内未退出，日志停在「Waiting for background tasks to complete」——浏览器/监控端仍有 `/stream` 长连接，与此前记录的退出挂起一致 |
| 13:33 | `kill -9 45933` | 进程已停止；无 main.py 残留；8000 端口已释放 |

| 13:38 | 用户询问 README 是否补部署方法；通读 README.md（97 行）评估 | 现有「快速开始」只写了通用 venv + requirements，在 Orin 上按此安装会失败；结论建议单列 `docs/deploy-jetson.md`，README 只留短小节与链接（待用户确认） |

| 13:39 | 用户同意补部署文档；核对素材：`requirements.txt`、`pip show torch torchvision numpy`、两者的 `direct_url.json`、`/tmp/tv_build`、cuSPARSE Lt 软链 | 取到 torch 轮子确切 URL 与 sha256（`6f75fd2d…`）；确认 torchvision 由 `/tmp/tv_build/vision-v0.20.0` 本地安装；软链指向 `../../nvidia/cusparselt/lib/`；hf-mirror 模型链接 200、6,534,387 字节 |
| 13:40 | 新建 `docs/deploy-jetson.md`（157 行） | 9 步安装顺序（venv → torch → cuSPARSE Lt → numpy → torchvision → 其余依赖 → 模型 → 相机 → 启动），附已知问题与踩坑速查表；注明实测版本与网络前提 |
| 13:41 | 修改 `README.md` | 「平台差异」前加「Jetson / Orin（CUDA）」小节并链到新文档；模型下载补 hf-mirror 备选；平台表修正 `check_camera.py` 误推红外节点；文档表加一行 |
| 13:41 | 复查 `git diff` 与工作区 | README 7 增 2 删；`docs/deploy-jetson.md`、`docs/deploy-log.md` 为新增未跟踪文件；服务保持停止状态，未启动 |

| 13:44 | 用户问「这是最简单的方法吗」；检查 ultralytics NMS 选择逻辑与 Docker 条件 | `nms.py` 在 CUDA 张量上直接用 `torchvision.ops.nms`，无 try/except 回退；`autobackend.warmup` 无条件 `import torchvision` → 必须有能在 GPU 上算 NMS 的 torchvision；Docker 与 containerd 已装且免 sudo 可用 |
| 13:46 | 测 Docker Hub 连通性：`curl registry-1.docker.io/v2/`、`docker manifest inspect ultralytics/ultralytics:latest-jetson-jetpack6` | 均超时（GitHub 受限的同时 Docker Hub 也不通），官方 Jetson 镜像路径在本机不可用；结论：源码编译仍是唯一可复现方案 |

<!-- 后续操作追加于此 -->
