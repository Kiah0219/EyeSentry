"""列出可用相机与各自实际支持的格式，用来决定 config.yaml 里的 camera.source。

Linux 上一台 RealSense 会占 5~6 个 /dev/videoN（深度 / IR / 彩色 / 元数据），
彩色流是同时支持 YUYV 与 MJPG 的那个；本工具用 MJPG 打开来验证能不能出图。

用法：
    python tools/check_camera.py
    python tools/check_camera.py --width 1280 --height 720
"""

from __future__ import annotations

import argparse
import glob
import platform
import sys

import cv2

DEFAULT_SIZE = (1280, 720)


def candidates() -> list:
    system = platform.system()
    if system == "Linux":
        devices = sorted(glob.glob("/dev/video*"), key=lambda p: int("".join(filter(str.isdigit, p)) or 0))
        if devices:
            return devices
    # macOS / Windows 用序号探测；Linux 无设备节点时也退回序号
    return list(range(6))


def backend_for(source):
    if isinstance(source, str) and source.startswith("/dev/"):
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


def decode_fourcc(value: float) -> str:
    code = int(value)
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return "".join(ch for ch in text if ch.isprintable()).strip() or "?"


def probe(source, size):
    cap = cv2.VideoCapture(source, backend_for(source))
    if not cap.isOpened():
        cap.release()
        return None

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
    cap.set(cv2.CAP_PROP_FPS, 15)

    row = {
        "source": str(source),
        "fourcc": decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)),
        "size": (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "readable": False,
    }
    ok, frame = cap.read()
    if ok and frame is not None:
        row["readable"] = True
        row["size"] = (frame.shape[1], frame.shape[0])
    cap.release()
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="探测可用相机")
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1])
    args = parser.parse_args()

    size = (args.width, args.height)
    print(f"平台 {platform.system()} · 探测分辨率 {size[0]}x{size[1]} · OpenCV {cv2.__version__}\n")

    rows = []
    for source in candidates():
        if isinstance(source, str) and not glob.glob(source):
            continue
        row = probe(source, size)
        if row is not None:
            rows.append(row)

    if not rows:
        print("没有找到可用相机。")
        print("  1. 数据线要插 USB3 口，别用只能充电的线")
        print("  2. macOS 首次运行要在「系统设置 → 隐私与安全性 → 相机」里允许终端")
        print("  3. Linux 看内核有没有认出来：lsusb && v4l2-ctl --list-devices")
        return 1

    print(f"{'设备':<16}{'格式':<8}{'分辨率':<12}{'帧率':<8}{'能出图'}")
    print("-" * 56)
    for row in rows:
        w, h = row["size"]
        print(
            f"{row['source']:<16}{row['fourcc']:<8}{f'{w}x{h}':<12}"
            f"{row['fps']:<8.1f}{'是' if row['readable'] else '否'}"
        )

    usable = [r for r in rows if r["readable"] and min(r["size"]) >= 480]
    print()
    if usable:
        best = max(usable, key=lambda r: r["size"][0] * r["size"][1])
        print("建议写入 config.yaml：")
        print("  camera:")
        print(f"    source: {best['source']}")
        print(f"    width: {best['size'][0]}")
        print(f"    height: {best['size'][1]}")
        print(f"    fourcc: {best['fourcc']}")
        if len(usable) > 1:
            others = ", ".join(r["source"] for r in usable if r is not best)
            print(f"\n另有可用设备：{others}（想用就改 source）")
    else:
        print("设备能打开但读不到图。多节点设备（如 RealSense）要选支持 MJPG 的那个节点。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
