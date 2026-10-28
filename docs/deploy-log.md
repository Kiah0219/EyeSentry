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

| 13:48 | 查看远端与分支 | `origin` 为 `git@github.com:Kiah0219/EyeSentry.git`；`main` 跟踪 `origin/main`；工作区 5 项改动待提交 |
| 13:48 | 分两笔提交 | `939f8e9` fix：修复相机热切换黑屏（`stream.py`）+ 默认相机改彩色节点（`config.yaml`）；`88e2a94` docs：Jetson 部署文档与操作台账（`README.md` + 2 个 docs 文件） |
| 13:49 | `git push origin main` | 成功：`95c14fb..88e2a94 main -> main`；SSH 加 10s 连接超时，一次通过 |
| 13:49 | `git switch -c dev` | 本地 `dev` 分支，基于 `88e2a94`，未推远端 |
| 13:49 | 在 dev 上启动服务 | PID 60921；`/dev/video4` 1280x720@15 YUYV；检测器就绪；uvicorn 监听 8000；本体通道仍因缺 ffplay 只打日志 |
| 13:50 | 相机切换回归（dev 分支代码） | `/dev/video2` → HTTP 200、14.2 FPS；切回 `/dev/video4` → HTTP 200、15.0 FPS、1 个检测框；截图 `/tmp/snap_dev.jpg` 为彩色画面，中文标签「人 0.87 · 3.3s」 |

| 14:05 | 用户询问 cuSPARSE Lt 与软链的原因及复现方式；复核 `readelf -d` / `ldd` | `libtorch_cuda.so` 的 NEEDED 含 `libcusparseLt.so.0`，RUNPATH 为 `$ORIGIN:/usr/local/cuda/lib64/stubs:/usr/local/cuda/lib64`；`ldd` 已通过 `torch/lib` 软链解析到 venv 内 421 MB 的实现。未做任何修改 |

| 14:09 | 按用户要求把 cuSPARSE Lt 的原理、软链理由与撤销命令补入 `docs/deploy-jetson.md` 第 3 步 | 新增 NEEDED 依赖、RUNPATH `$ORIGIN` 说明与 `ldd` 验证；补撤销步骤（删除软链 + 卸载轮子）。代码与服务未动 |
| 14:11 | 切到 main 提交时发现 `docs/deploy-log.md` 残留 stash 冲突标记，且已进入本地提交 58390a7（未推送） | 切换分支时环境自动 stash/pop 引发冲突；`git reset --soft HEAD~1` 撤回该提交，改动全部保留 |
| 14:12 | 解决冲突：保留 Stashed changes 一侧（13:48–14:09 记录完整），删除 3 行冲突标记 | `grep` 复查两个文档均无冲突标记；暂存区 2 文件、28 增 2 删 |
| 14:12 | `git push origin main` | 成功：`88e2a94..cd1328c main -> main`；`dev` 仍停在 3aae75b，落后 main 一个文档提交；切换分支时产生的 `stash@{0}` 保留未删 |
| 14:12 | 把以上过程追加到台账，单独提交并推送 | 保持 main 工作区干净；本条为本次最后一次记录提交 |
| 14:15 | 用户要求删除本地 `dev`；删除前 `git diff dev main --stat` 确认 main 为超集 | 差异只有两个 docs 文件、main 多 22 行，dev 无独有内容；`git branch -D dev` 成功（原指向 3aae75b）；`stash@{0}` 仍保留该提交对象 |
| 14:15 | 将删除操作写入台账并提交推送 | 当前仅 `main` 一个本地分支，工作区只剩未跟踪的 `.qoder/` |


## 2026-10-26（第二台机器：JetPack 7 / Orbbec 前胸相机）

- 目标机器：Jetson Orin，L4T R39.2.1（JetPack 7）/ Ubuntu 24.04.4 / Python 3.12.3 / CUDA 13.2 / 驱动 595.78 / cuDNN 9.20，算力 sm_87，12 核
- 与上面一台不是同一台机器：这台根分区只剩 2 GB（`/data` 有 334 GB），项目实际在 `/data/EyeSentry`，相机由 cockpit 中间件托管
- 用户要求：先试 Orbbec 相机；不要为此升级已有的 torch，尽量少动机器人上的东西

| 时间 | 操作 | 结果 |
|---|---|---|
| 14:24 | 环境探测：`lsb_release`、`/etc/nv_tegra_release`、`nvcc --version`、`nvidia-smi`、`df -h`、`/dev/video*` | JetPack 7 / CUDA 13.2 / 驱动 595.78；12 个 video 节点（G300 深度+彩色+双目红外、s56、shw5g）；根分区 2.0 GB 可用，`/data` 334 GB |
| 14:28 | 读 cockpit 侧配置与进程：`/opt/cockpit/README.md`、`ps` | Orbbec 335Lg（前胸相机）由 root 起的 `cockpit-camera-orbbec` 采集，发布 `/cockpit/camera/chest/main/color`（1280x720 rgb8），不是 `/dev/video` 节点 |
| 14:36 | `python3 -m venv .venv`（项目内） | 成功，Ubuntu 24.04 自带 ensurepip；`include-system-site-packages = false`，后续安装全部锁在 `.venv` 内 |
| 14:38 | 查 CUDA 13 版 torch 来源：`pypi.jetson-ai-lab.io/jp7/cu130`、NVIDIA `developer.download.nvidia.com/.../jp/v62、v70、v71` | jetson-ai-lab 的 jp7 目录 404；NVIDIA redist 无 JetPack 7 目录；改用官方 `download.pytorch.org/whl/cu130` |
| 14:40 | 下载 `torch-2.9.1+cu130-cp312-cp312-manylinux_2_28_aarch64.whl`（489 MB） | 官方索引有 aarch64+cp312 的 cu130 轮子，依赖全是 `nvidia-*-cu13`，逐个核对均有 aarch64 轮子 |
| 14:44 | GMSL 相机直连验证：`v4l2-ctl --list-formats-ext`、按节点抓帧 | G300 彩色 `/dev/video3`（BA81 Bayer）`EBUSY`（被 cockpit 占用）；`/dev/video4`、`/dev/video6` 能开但是绿色红外图；结论：这台机器的相机不能按 RealSense 那套 UVC 方式直接用 |
| 14:50 | 试直接订中间件：`yirt2 topic list`、`/dev/shm/fastrtps_*` 权限、`ls -l /tmp/roudi` | YiRT2 需要 root 的 `/tmp/roudi`（root:root 660），RMW 走 `fastdds-shm-large.xml` 纯共享内存，段文件 root 644；普通用户 `open` 报 Errno 13 → 非 root 拿不到帧 |
| 14:56 | 找 cockpit 的 HTTP 服务：`ss -ltnp`、`journalctl | grep 监听` | 端口 8080 是无关的 abx 页面（POST 返回 501）；真正控制台服务在 `0.0.0.0:50919`，日志里有「HTTP 监听地址=http://0.0.0.0:50919/」 |
| 15:00 | 手探 API：`POST /session/open` → `GET /api/v1/cameras` → `POST /preview/select` → `GET /preview/image.mjpg` | 会话是独占的（占用时 409），心跳停约 60 秒后过期；相机目录里 Orbbec 为 `author=ORBBEC id=chest channel=main model=ORBBEC:335lg` |
| 15:02 | 用一次性脚本抓一帧存 `.cache/frames/orbbec.jpg` | 159 KB，1280x720 彩色，画面是实验室与机器人，确认这条 HTTP 通路能拿到可用彩色帧 |
| 15:55 | 下载 `ultralytics-8.4.189` 轮子读源码，确认 NMS 选择逻辑 | `utils/nms.py:99` 以 `"torchvision" in sys.modules` 决定是否用 torchvision 的 CUDA NMS；`nn/autobackend.py:377` 在非 end2end 模型 warmup 时无条件 `import torchvision` → 必须有能用的 torchvision |
| 15:57 | 从 Gitee 镜像取 `vision-v0.24.1` 源码（14 MB）备用 | 准备按上一台机器的做法源码编译 CUDA 版 torchvision |
| 16:14 | 写 `tools/cockpit_mjpeg_bridge.py`：以只读方式消费 cockpit 预览，本机重新发布 MJPEG | 与网页预览用的是同一组接口（`session/open`、`session/heartbeat`、`cameras`、`preview/select`、`preview/image.mjpg`）；默认 `open` 不 `takeover`，被占用时等；修掉 `preview` 未初始化、重复发同一帧、选相机等待逻辑三处问题 |
| 16:28 | `setsid nohup python3 tools/cockpit_mjpeg_bridge.py &`（系统 python3，仅标准库） | 取得会话 generation=3，`http://127.0.0.1:8091/stream` 开始出帧；`/status` 显示 15 fps、单帧约 157 KB |
| 17:16 | 后台安装 torch 完成（清华镜像 + `PIP_CACHE_DIR=/data/EyeSentry/.cache/pip`） | 装齐 `nvidia-*-cu13` 与 triton 3.5.1；venv 4.3 GB，缓存留在 `/data`，不写根分区 |
| 17:18 | 验证 CUDA：`import torch`、`get_arch_list()`、2048² matmul、cudnn 卷积 | torch 2.9.1+cu130，`cuda_avail True`，设备 Orin，cc 8.7 / 16 SM；轮子只带 sm_80 起的 SASS，靠同大版本向前兼容在 sm_87 上跑通，matmul 与 conv 结果正常 |
| 17:20 | 发现官方 cu130 索引其实有 aarch64+cp312 的 torchvision（0.25.0+cu130），但只配 torch 2.10 | 曾起过一条「torch 升到 2.10.0+cu130 + torchvision 0.25.0+cu130」的后台安装，避免源码编译 |
| 17:44 | 用户要求不要升级、尽量少动机器人；`pkill -f 'torch==2\.1[0]'` 终止升级 | 升级停在下载阶段，未执行卸载/覆盖：`site-packages` 仍是 `torch-2.9.1+cu130.dist-info`，torch 未被改动 |
| 17:45 | 改查 `cu130/torchvision/` 里无 `+cu130` 标记的 `torchvision-0.24.1-cp312-...-aarch64.whl`：解包看 `_C.so` | `NEEDED` 含 `libtorch_cuda.so` / `libc10_cuda.so` / `libcudart.so.13`，并带 `nms_kernel_cu` 内核与打包的 `libnvjpeg`/`libcudart` → 这就是配 torch 2.9.1 的 CUDA 构建，不需要编译 |
| 17:47 | 安装其余依赖（opencv-python 4.11.0.86、matplotlib、pillow、PyYAML、requests、psutil、polars、nvidia-ml-py、ultralytics-thop、numpy 2.5.4、fastapi、uvicorn[standard]、edge-tts、lap）+ `--no-deps` 装该 torchvision 轮子与 ultralytics 8.4.189 | 三项退出码均 0；`pip check` 只报 ultralytics 想装可选的 `ultralytics-platform`，不影响导入与推理 |
| 17:47 | 从 hf-mirror 取 `models/yolov8n.pt` | 6,534,387 字节，与上一台机器一致；sha256 `31e20dde…` |
| 17:48 | 验证 GPU NMS：`torchvision.ops.nms`（float 框，CUDA 张量） | 返回 `tensor([0, 2], device='cuda:0')`，CUDA NMS 可用（注意 boxes 必须是 float，传 Long 会报 `"nms_kernel" not implemented for 'Long'`） |
| 17:49 | 给 `camera.py` 加 HTTP MJPEG 后端 | 系统 OpenCV 4.14 是 `FFMPEG: NO`，`VideoCapture("http://…")` 打不开；新增 `HttpMjpegCapture`（标准库 `urllib` 拉流 + 按 SOI/EOI 拆帧 + `cv2.imdecode`，后台线程、断流 1 秒重连、`release()` 关连接并 join），`Camera.open()` 走 http 分支并报 `backend="http" fourcc="JPEG"`；实测 30 帧 / 2.02 s ≈ 14.9 fps，退出干净 |
| 17:49 | 配套改动：`list_sources(active_source, default_source)` 把 http 源也列出来；`Runtime.switch_camera` 允许 `backend in ("uvc","http")`；`config.yaml` 改为 `source: http://127.0.0.1:8091/stream`、`fourcc: ""` | 否则切到 UVC 后网页上就再也选不回 Orbbec |
| 17:49 | 真实帧跑 `PyDetector.process` | 首帧 2786 ms（预热），之后 35–44 ms/帧（约 23–27 FPS），设备 `cuda:0`，检出「椅子」等目标 |
| 17:50 | `setsid nohup .venv/bin/python main.py >> /tmp/eyesentry.log 2>&1 &` | 相机以 http 后端打开 1280x720@15；检测器就绪；uvicorn 监听 `0.0.0.0:8000`；警告找不到本体播放器（本机无 ffplay/afplay） |
| 17:50 | 验证 `GET /api/status`、`/api/snapshot.jpg`、`/stream` | status：`connected true`、`fps 15.0`、`last_frame_age_s 0.01`；snapshot 200 / 153 KB；`/stream` 返回 `multipart/x-mixed-replace; boundary=frame`；截图见实验室全景与中文标签「人 0.58 · 1.2s」 |
| 17:51 | 验证停留告警链路 | 告警持续写入 `data/alerts.jsonl`（含 `text`、`audio_url`、`snapshot_url`），`data/snapshots/*.jpg` 与 edge-tts 合成的 MP3 均生成；本体播放因缺播放器只记日志，网页通道不受影响 |
| 17:52 | 复核改动范围：`df -h`、`du -sh .venv .cache`、`pgrep -af /data/EyeSentry/.venv`、系统 `python3 -c "import torch"` | venv 4.9 GB + 缓存 4.7 GB 全在 `/data`；根分区未增长；系统 Python 本来就没有 torch，别的程序不受影响；项目外只多出 `~/.config/Ultralytics/settings.json`（ultralytics 首次运行自建） |
| 18:56 | 用户反馈本体不出声；探测播放通路：`command -v ffplay/ffmpeg/aplay/paplay/pw-play/sox/gst-launch-1.0`、`/proc/asound/cards`、`wpctl status` | 没有 ffmpeg，但有 `aplay` / `paplay` / `pw-play` / `sox` / `gst-launch-1.0`；声卡三张：USB `AIM626U1-8CH260728`（机器人功放）、tegra HDA、tegra APE；PipeWire 默认 sink 是 USB 那张，音量 0.30 |
| 18:58 | 逐个试解 MP3：`sox x.mp3 -n stat`、`gst-discoverer-1.0 file://…` | sox 报 `no handler for file extension 'mp3'`；GStreamer 正常识别（MPEG layer III / 24 kHz / mono / 48 kbps）→ 用 gst 播，不必装 ffmpeg |
| 18:59 | 确认音频真到硬件：播放时读 `/proc/asound/card0/pcm0p/sub0/status` | `state: RUNNING`、`owner_pid 2456`（pipewire）、`hw_ptr` 递增 → 数据确实推到 USB 功放；`pactl` / `wpctl` 在没有 `XDG_RUNTIME_DIR` 的 shell 里连不上，补 `XDG_RUNTIME_DIR=/run/user/1000` 即可 |
| 19:01 | 修改 `tts.py`：`_device_command` 在 ffplay 之后回退到 `gst-launch-1.0 -q playbin uri=…`；新增 `_player_env()` 在缺少 `XDG_RUNTIME_DIR` 时补 `/run/user/<uid>` | 第一次改完启动即崩：探测用的相对路径 `Path("x")` 调 `as_uri()` 抛 `ValueError`，改成 `path.resolve().as_uri()` |
| 19:02 | 重启服务；旧进程被浏览器（192.168.50.6）挂着的 `/stream` 卡住，SIGTERM 8 秒未退 | 与上一台机器记的同一问题；给 `main.py` 的 `uvicorn.run` 补 `timeout_graceful_shutdown=5`，本次先 `kill -9` 后重启 |
| 19:03 | `config.yaml`：`dwell.cooldown_seconds` 30 → 120 | 冷却按跟踪 ID 记（`detector.py:112`），同一个人被 ByteTrack 换 ID 就重新计时，实测约每 5 秒一报；不改算法，先放大冷却并在注释里写明原因 |
| 19:05 | 用 `tts._device_command` + `_player_env` 原样复现一次播报 | 命令 `gst-launch-1.0 -q playbin uri=file:///…/20261026-190508-89.mp3`，播放中 `card0/pcm0p/sub0` 为 `RUNNING`，退出码 0；重启后的日志里再无「无可用播放器」 |

### 这台机器的已知问题

- **Orbbec 只能走 cockpit 的 HTTP 预览**：非 root 拿不到 YiRT2 / RMW 共享内存；桥接和网页预览共用一个独占会话，网页上点同一个 preview 会把桥接顶掉（桥接会自动重连），必要时用 `--takeover` 主动接管。（2026-10-27 14:28 起已改为 root 下 yirt2 订阅 topic，此条仅作旧方案记录）
- **桥接重启可能要等**：旧会话的心跳停止后约 60 秒才过期，这期间 `session/open` 会返回 409，桥接按设计等待而不是接管。
- **告警冷却按跟踪 ID 算**：`DwellTracker` 的冷却记在 `_last_trigger[tid]`（`detector.py:112`），同一个人被 ByteTrack 换 ID 就会重新计时，30 秒冷却实测退化成约每 5 秒一报。本次只在 `config.yaml` 把冷却放大到 120 秒并写明原因，没有改算法；要真正限流应改成全局冷却或按人在场状态限流。
- **本体播报走 GStreamer**：本机没有 ffmpeg，`tts.py` 在 ffplay 之后回退到 `gst-launch-1.0 playbin`；从 nohup / SSH 启动时进程没有 `XDG_RUNTIME_DIR`，连不上 PipeWire，`_player_env()` 会补上。默认 sink 是 USB 功放 `AIM626U1`，音量只有 0.30，嫌小声可以 `wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.6`（需要 `XDG_RUNTIME_DIR=/run/user/1000`）。
- **root 分区只剩 2 GB**：pip 缓存与 venv 都放在 `/data/EyeSentry`，别往 `/` 或 `/tmp` 写大文件。
- **未做开机自启**：桥接与 EyeSentry 都是手动后台进程，重启机器后需要再起一遍（systemd 单元待补，需要 sudo）。


## 2026-10-27（同一台机器：本体喇叭）

| 时间 | 操作 | 结果 |
|---|---|---|
| 10:12 | 发现机器在 10:02 重启过（内核日志有 APE graph 声卡注册），`/tmp` 已清空 | 桥接、EyeSentry、日志全部消失；声卡编号也变了（card0=HDA、card1=USB、card2=APE） |
| 10:13 | 重启桥接 `tools/cockpit_mjpeg_bridge.py` | 取得新会话 generation=1，`8091` 恢复出帧（约 112 KB/帧） |
| 10:13 | 排查「本体没声」：`command -v ffplay/ffmpeg/sox/gst-launch-1.0`、`/proc/asound/cards`、`wpctl status`、`pactl list sinks` | 无 ffmpeg；sox 不认 mp3；GStreamer 可解 24 kHz 单声道 mp3。**默认 sink 是 USB 的 `AIM626U1`（麦克风阵列板，stereo 输出不是喇叭）**，真正的喇叭在 APE → es8326（I2C `6-0019`），且该 sink 当时激活端口是 `Headphones` |
| 10:13 | A/B 试音：先 USB 后 APE，各 3 秒 880 Hz；再把 APE 端口切到 `[Out] Speaker` | 前两次没声；切到 Speaker 后出声（播放时 `card2/pcm0p/sub0` 为 `RUNNING`），确认本体喇叭在 APE/es8326 的 Speaker 通路 |
| 10:13 | 用户反馈「刚刚有声音了，不过太大了」，问当时音量 | 那次是 sink 100%（0.00 dB）+ 满幅正弦测试音；比人声响得多 |
| 10:13 | 用户要求「不要改系统的东西，改你自己的配置」；还原系统侧：默认 sink 回 USB、USB sink 30%、APE sink 40%、USB `PCM` 48% | 只保留 APE 的 `[Out] Speaker` 端口（Headphones 不出声，且该选择由 WirePlumber 记住） |
| 10:13 | 改 `tts.py` / `config.yaml`：新增 `tts.sink`（按前缀匹配，兼容 pipewire 的 `.N` 后缀）与 `tts.volume`（应用侧音量，与系统音量相乘） | 播放命令带上 `audio-sink=pulsesink device=…` 与 `volume=…`；`_play_loop` 同时补 `env=_player_env()`；每台机器可在配置里各自指定输出，不动系统默认 |
| 10:14 | 重启 EyeSentry | 日志：`本体播放输出：alsa_output.platform-sound.HiFi__hw_APE__sink.2，音量 0.6`（系统 40% × 0.6 ≈ 24%）；告警播放时 `card2/pcm0p` 为 `RUNNING`，声音走本体喇叭 |

## 2026-10-27（同一台机器：根分区瘦身）

| 时间 | 操作 | 结果 |
|---|---|---|
| 13:05 | 盘点根分区占用：`df -h / /data`、`du -sh /home/robot/*`、`du -sh /home/robot/.cache/*` | `/` 49G 用 96%；本项目在根分区只留了 `~/.config/Ultralytics/settings.json`（8K）。`.venv` 4.9G、`.cache` 4.7G 本来就在 `/data`，无需搬 |
| 13:06 | 核对用户说的 `/userdata`：`ls -ld /userdata`、`findmnt /data`、`/etc/fstab` | `/userdata` 不存在；fstab 里 `by-partlabel/userdata` 实际挂载在 `/data`（369G，余 337G） |
| 13:08 | 检查 `~/.vscode-server` 是否在用：`ps` 过滤 `vscode-server`、`lsof +D` | 无进程占用（只有 Qoder 的 `~/.qoder-cn` 进程），可安全迁移 |
| 13:09 | 复制 `~/.vscode-server`（2.2G / 24021 条目）到 `/data/EyeSentry/.runtime/vscode-server`，校验字节数与条目数一致后删原目录，建软链接 | `/data` 根目录是 root:root 755 且无免密 sudo，只能落在项目内的隐藏目录；软链接 `/home/robot/.vscode-server -> /data/EyeSentry/.runtime/vscode-server` 解析正常；`/` 由 96% 降到 92%（4.1G 可用） |
| 13:10 | `.gitignore` 补 `.cache/`、`.runtime/` | 本地运行时目录不入库 |
| 13:10 | 回归：`/api/status`、`8091/status` | 14.7 fps、桥接 streaming，迁移未影响运行中的进程 |
| 13:13 | 用户执行 `sudo mkdir -p /data/vscode-server && sudo chown robot:robot /data/vscode-server` | 目标目录就绪 |
| 13:14 | 把 `.runtime/vscode-server` 平移到 `/data/vscode-server`，`ln -sfn` 更新软链接 | `mv src/. dst/` 报 `Device or resource busy`（不能重命名 `.`），改用 `find -mindepth 1 -maxdepth 1 -exec mv -t` 逐项移动（同一文件系统，瞬间完成）；校验 2194457722 字节 / 24021 条目一致；`.runtime` 清空后删除；最终软链接 `/home/robot/.vscode-server -> /data/vscode-server` |

> 编辑器缓存现已统一放在 `/data/vscode-server`（userdata 分区根目录），项目内不再保留副本；VS Code 远程窗口重连一次即可。

| 14:05 | 排查机器人语音对话容器的播放通道：`docker ps`、`docker inspect`、进程 `/proc`、`robot-client.log` | 播音在 `aibao-robot-client`：挂宿主 `/run/user/1000/pulse/native`，`paplay --raw --device <sink>`，sink 由 `mixer-default` 跟随宿主默认；状态写入 `state/audio-route.json`；采集侧 `parecord` 走 USB `analog-surround-71.2` |
| 14:10 | 按“系统默认就是本体发声设备”修正 EyeSentry 策略：`config.yaml` 的 `tts.sink` 由固定 APE 改为 `auto`；`tts.py` 新增 `_default_sink()`，播放前解析、失败换默认 sink 重试一次 | 与语音对话容器同一条通道；声卡重枚举、sink 名加 `.N` 也不受影响 |
| 14:13 | 重启 EyeSentry（SIGTERM 后 `setsid -f` 拉起） | 日志：`本体播放输出：跟随系统默认（当前 alsa_output.usb-Actions_AIM626U1-…-analog-stereo.2），音量 0.6（与系统音量相乘）` |
| 14:13 | 用新解析结果实播一段告警 mp3（30% × 0.6）并读硬件状态 | `pactl list short sink-inputs` 有 24 kHz 单声道流；`/proc/asound/card1/pcm0p/sub0/status` 为 `RUNNING`、`hw_ptr` 递增，APE `card2/pcm0p` 为 `closed` → 默认 sink 确实驱动本体声卡硬件 |

## 2026-10-27（同一台机器：相机改走 ROS2 topic）

| 时间 | 操作 | 结果 |
|---|---|---|
| 14:20 | 查 cockpit 相机 topic：`yirt2 topic list/info`（domain 0）、`ros2 topic list`（domain 190 是语音的） | 彩图 `/cockpit/camera/chest/main/color`（`sensor_msgs/msg/Image`，rgb8 1280×720 30 fps，BEST_EFFORT，发布者 `_CREATED_BY_BARE_DDS_APP_`），另有 depth |
| 14:22 | 用标准 rclpy / 原版 FastDDS 订阅（robot 与 root 都试） | 订阅能匹配（`Subscription count 1`），但 robot 收 0 帧、root 只偶发 1 帧：cockpit 的 RouDi socket `/tmp/roudi` 与 `/dev/shm/iceoryx_mgmt` 都是 root:root 0660，且改过 FastDDS 主机身份、走 128 MB SHM 段 |
| 14:24 | 改用 cockpit 自带 `yirt2` 接口验证：`yirt2 topic hz`（root） | 稳定 29.5–30 Hz → 帧可以拿到，但必须以 root 读取 |
| 14:26 | 从 `/usr/share/cockpit-mid/wheels` 装 `yirt2`、`rmw_fastdds` 到 `.venv`（`--no-index`） | 不联网、不动系统 Python |
| 14:28 | 新增 `tools/yirt2_mjpeg_bridge.py`：`ImageSubscriber` 订阅 topic → BGR → JPEG → 本地 MJPEG（`/stream`、`/snapshot.jpg`、`/status`），10 秒无帧自动重订阅；非 root 直接报错退出 | 接口与旧 cockpit 桥接一致，EyeSentry 侧只改 URL |
| 14:30 | 在 root 容器里试跑（8092）：`received` 约 30 fps | 最初在回调里按时间门控编码，只有约 12 fps |
| 14:33 | 改成独立编码线程：回调只存最新帧，编码线程按 15 fps 取最新帧编码 | `received` 30 fps、`encoded` 15.0 fps，快照 1280×720 可解码，流约 150 KB/帧，容器 CPU 约 25%（单核占比） |
| 14:34 | 固化为常驻容器 `eyesentry-camera-bridge`：镜像 `omni-agent-robot-universal:0.2.6-96bcd828`，`--network host --ipc host -u 0 --restart unless-stopped`，`/data /usr /lib /etc /opt/cockpit` 只读挂载、`/tmp` 读写 | 仍以 root 跑（RouDi 与 SHM 权限所需），但不改宿主机服务和系统配置；Docker 自启后桥接自动恢复 |
| 14:35 | `config.yaml` 的 `camera.source` 改为 `http://127.0.0.1:8092/stream`，重启 EyeSentry | `/api/status`：`connected=true`、14.9 fps、1280×720、检测框正常；`/api/snapshot.jpg` 与 `/stream` 均出图 |
| 14:37 | 停掉旧的 cockpit Web 预览桥接（8091，`tools/cockpit_mjpeg_bridge.py`） | 新链路不再占用 cockpit 控制台会话，网页端看预览不会互相顶掉 |
| 14:45 | 删除 `tools/cockpit_mjpeg_bridge.py`（从未入库，`git status` 里是未跟踪文件），`yirt2_mjpeg_bridge.py` 默认端口改为 8092 | 项目中不再保留 8091 的 HTTP 预览逻辑；`grep` 确认无残留引用，8092 之外的桥接端口已无监听 |

> 两处遗留：EyeSentry 本身仍是手动后台进程，重启机器后要手动拉起（桥接容器会自启）；桥接依赖本机已有的 aibao 镜像，镜像被删后容器无法重建。（2026-10-28：开发期改为不依赖 Docker / systemd，全部手动进程，见文末。）

### 相机帧是怎么拿到的（cockpit middleware + yirt2）

- **相机由 cockpit 独占**：`cockpit-camera-orbbec` 打开 Orbbec 335Lg，通过 cockpit 自己的 middleware 发布，DDS domain 0。彩图 topic 是 `/cockpit/camera/chest/main/color`（`sensor_msgs/msg/Image`，1280×720 rgb8，30 fps，QoS `BEST_EFFORT`），另有 `/depth`。用 `ros2 topic list`（默认 domain 0）能看到它，但普通 `ros2 topic echo` 收不到数据。
- **传输走 iceoryx 共享内存**：cockpit 用 `libcockpit_mid_yirt2.so.1` + 补丁版 FastDDS（`libcockpit_mid_rmw_fastdds.so.1`），底层是 iceoryx 2.0.7。`iox-roudi` 以 root 运行：`/tmp/roudi`（root:root 0660）负责进程注册，`/dev/shm/iceoryx_mgmt`（root:root 0660）管理 SHM 段，profile `/usr/share/cockpit-mid/config/fastdds-shm-large.xml` 把单段放大到 128 MB。像素数据只在共享内存里搬，不走 UDP/TCP。
- **标准 rclpy 为什么不行**：`rmw_fastrtps` 能通过 discovery 匹配到 publisher（`ros2 topic info -v` 显示 `Subscription count 1`），但打不开 `/tmp/roudi`；用 root + 原版 FastDDS 能连上，可 cockpit 的补丁版修改了共享内存命名/主机身份，实测 8 秒只到 1 帧。单独切 UDP profile 也拿不到帧。
- **yirt2 是 cockpit 自己的 Python 接口**：wheel 在 `/usr/share/cockpit-mid/wheels/yirt2-1.0.0-cp312-cp312-linux_aarch64.whl`（同目录还有 `rmw_fastdds`），装进项目 venv 即可，不碰系统 Python。用法是 `yirt2.ImageSubscriber().init(callback, topic, "/", yirt2.ImageSubscriberOptions())`，回调里的 `Image` 带 `.header`、`.info`（width/height/encoding/step）和原始 `.data` 字节。它内部用的正是补丁版 RMW，所以必须以 root 运行；root 下 `yirt2 topic hz` 稳定 29.5–30 Hz。
- **桥接怎么转成 MJPEG**：`_on_image` 只保存最新帧并通知；独立编码线程按 `--fps`（15）取最新帧，把 rgb8 重排成 BGR（`np.frombuffer` → `reshape(height, step)` → 去掉行尾 padding），`cv2.imencode` 成 JPEG，再交给本地 HTTP 服务以 `multipart/x-mixed-replace` 输出 `/stream`，`/snapshot.jpg` 取最新一帧，`/status` 返回 `state/topic/frames/received/encoded/last_frame_age_s`。超过 `--stale-seconds`（10 秒）没有新帧就 `sub.shutdown()` 后重新 `init()`，相机重启或 cockpit 重载都能自愈。
- **排查动作**：`yirt2 topic list`（有哪些 topic）、`yirt2 topic info /cockpit/camera/chest/main/color`（类型与 QoS）、`yirt2 topic hz …`（root 下确认真的有帧）、`curl 127.0.0.1:8092/status`（桥接自己的收发计数和最后一帧大小）。

## 2026-10-28（开发期部署方式：不用 Docker / systemd）

| 时间 | 操作 | 结果 |
|---|---|---|
| 11:43 | 机器再次重启（当天第二次） | 桥接与 EyeSentry 都没有自启；`8092`、`8000` 无监听 |
| 13:05 | 复查 Docker：`docker ps -a`、`docker inspect`、`docker start` | 所有容器 `Exited (255)`；启动报 `RWLayer ... is unexpectedly nil`、镜像 blob `not found`（存储路径 `/data/containerd`），`docker inspect/start` 均失效；`aibao-robot`、`aibao-robot-client` 同样起不来。Docker 本身 active，但镜像/层已不可用 |
| 13:06 | 复查宿主侧：`ps`、`ss -ltn`、`ros2 topic info -v` | EyeSentry、桥接均未运行；cockpit（roudi / camera-driver-orbbec / cockpit-web）正常，`/cockpit/camera/chest/main/color` 的 `Publisher count: 1` |
| 13:16 | 曾按“去掉 Docker 依赖”准备两个 systemd 单元（桥接 root、EyeSentry robot） | 单元只写入项目，未安装 |
| 13:18 | 用户要求开发期不要用 Docker 和 systemctl | 删除 `tools/systemd/` 下两个单元文件（未安装过）；桥接错误提示里的 systemd 建议也去掉 |
| 13:20 | 确定开发期运行方式：全部手动进程 | 桥接：`sudo setsid -f /data/EyeSentry/.venv/bin/python /data/EyeSentry/tools/yirt2_mjpeg_bridge.py --port 8092 >/tmp/camera-bridge.log 2>&1`；EyeSentry：`setsid -f .venv/bin/python main.py >>/tmp/eyesentry.log 2>&1`。待执行 |
| 13:26 | 用户手动以 root 启动桥接 | 子进程 PID 280292；`8092/status`：`streaming`，received≈30 fps、encoded 15 fps，单帧约 110 KB |
| 13:26 | 以 robot 启动 EyeSentry（`setsid -f`） | PID 285092；`/api/status`：`backend=http`、`source=8092`、15.0 fps、1280×720，检测框正常；`/api/snapshot.jpg` 返回 720×1280 |
| 13:26 | 首条告警 `20261028-132650-1600`（2 人，5.0 s） | edge-tts 合成报 `NoAudioReceived`，按 F-32 回退提示音；随后单次重试合成成功（18 KB），判定为 edge 服务偶发，不是代码问题。`speech.platform.bing.com` 的 IPv4 可达、IPv6 不通 |
| 13:27 | 页面把检测暂停（`detector_enabled=False`） | 画面仍 15 fps；为用户操作，未改代码 |

<!-- 后续操作追加于此 -->
