"""相机采集：打开、读帧、格式自检。

一台 RealSense 会占 5~6 个 /dev/videoN（深度 / IR / 彩色 / 元数据），
只有同时支持 YUYV 与 MJPG 的那个是彩色流；用 tools/check_camera.py 找。
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraInfo:
    source: str
    width: int
    height: int
    fps: float
    fourcc: str
    backend: str = "uvc"

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 2),
            "fourcc": self.fourcc,
        }


def _parse_source(raw):
    if isinstance(raw, int):
        return raw, cv2.CAP_ANY
    text = str(raw).strip()
    if text.isdigit():
        return int(text), cv2.CAP_ANY
    if text.startswith("/dev/"):
        return text, cv2.CAP_V4L2
    return text, cv2.CAP_ANY


def _video_order(path: Path) -> int:
    digits = "".join(ch for ch in path.name if ch.isdigit())
    return int(digits) if digits else 0


def probe_source(source) -> bool:
    """试开一次并读一帧；用于非 Linux 平台的设备枚举。"""
    cap = cv2.VideoCapture(*_parse_source(source))
    try:
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        return bool(ok and frame is not None)
    finally:
        cap.release()


def _is_http(source) -> bool:
    return isinstance(source, str) and source.startswith(("http://", "https://"))


def list_sources(active_source=None, default_source=None, probe_limit: int = 4) -> list[dict]:
    """枚举可选采集设备（F-06）。

    Linux 直接读 /dev/video*，用 sysfs 的名字，不打开设备——RealSense 的
    深度 / 红外节点也能打开，试开反而容易误导。其他平台没有设备名接口，
    只能按索引逐个试开，上限 probe_limit 个；正在使用的那个不重复打开。
    HTTP MJPEG 源不在设备枚举里，但当前或默认源是它时要列出来，否则切走就回不去。
    """
    active = None if active_source is None else str(active_source)
    items = []
    for source in (active, None if default_source is None else str(default_source)):
        if _is_http(source) and all(item["source"] != source for item in items):
            items.append(
                {
                    "backend": "http",
                    "source": source,
                    "name": "HTTP MJPEG",
                    "available": True,
                    "current": source == active,
                }
            )
    if sys.platform.startswith("linux"):
        for path in sorted(Path("/dev").glob("video*"), key=_video_order):
            try:
                name = (Path("/sys/class/video4linux") / path.name / "name").read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                name = ""
            items.append(
                {
                    "backend": "uvc",
                    "source": str(path),
                    "name": name or path.name,
                    "available": True,
                    "current": str(path) == active,
                }
            )
        return items

    for index in range(probe_limit):
        source = str(index)
        is_active = source == active
        items.append(
            {
                "backend": "uvc",
                "source": source,
                "name": f"相机 {index}",
                "available": True if is_active else probe_source(index),
                "current": is_active,
            }
        )
    return items


def _decode_fourcc(value: float) -> str:
    """macOS 的 AVFoundation 不上报 FourCC，返回 0 或 0xFFFFFFFF，这里统一当未知。"""
    code = int(value)
    if code <= 0:
        return ""
    text = "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4))
    return text if text.isascii() and text.isprintable() else ""


class HttpMjpegCapture:
    """MJPEG-over-HTTP 拉流，后台线程解析 multipart，逐帧 cv2.imdecode。

    系统 OpenCV 可能没有 FFmpeg 后端，VideoCapture 打不开 http:// 源；
    这里只依赖标准库和 OpenCV 的 JPEG 解码，不受编译选项影响。
    """

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout
        self._latest = None
        self._seq = 0
        self._read_seq = 0
        self._cond = threading.Condition()
        self._closed = threading.Event()
        self._resp = None
        self._thread = threading.Thread(target=self._run, name="http-mjpeg", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                with urllib.request.urlopen(self.url, timeout=self.timeout) as resp:
                    self._resp = resp
                    buf = b""
                    while not self._closed.is_set():
                        chunk = resp.read(16384)
                        if not chunk:
                            break
                        buf += chunk
                        while True:
                            start = buf.find(b"\xff\xd8")
                            if start < 0:
                                buf = buf[-1:]
                                break
                            end = buf.find(b"\xff\xd9", start + 2)
                            if end < 0:
                                buf = buf[start:]
                                break
                            jpeg = buf[start:end + 2]
                            buf = buf[end + 2:]
                            frame = cv2.imdecode(
                                np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR
                            )
                            if frame is None:
                                continue
                            with self._cond:
                                self._latest = frame
                                self._seq += 1
                                self._cond.notify_all()
            except Exception as exc:
                if self._closed.is_set():
                    return
                log.warning("HTTP 相机流中断：%s，1 秒后重连", exc)
                self._closed.wait(1.0)
            finally:
                self._resp = None

    def read(self, timeout: float = 1.0):
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq == self._read_seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            self._read_seq = self._seq
            return self._latest

    def release(self) -> None:
        self._closed.set()
        resp = self._resp
        self._resp = None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


class Camera:
    def __init__(self, source, width=1280, height=720, fps=15, fourcc="MJPG"):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.fourcc = fourcc
        self._cap = None
        self._mjpeg = None

    def open(self) -> CameraInfo:
        if _is_http(self.source):
            return self._open_http()

        src, backend = _parse_source(self.source)
        cap = cv2.VideoCapture(src, backend)
        if not cap.isOpened():
            raise CameraError(
                f"打不开相机源 {self.source!r}。先运行 python tools/check_camera.py 看可用设备。"
            )

        if len(self.fourcc) == 4 and self.fourcc.isascii():
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
        else:
            log.warning("fourcc %r 不是 4 个 ASCII 字符，跳过设置，用设备默认格式", self.fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

        info = CameraInfo(
            source=str(self.source),
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)),
            fourcc=_decode_fourcc(cap.get(cv2.CAP_PROP_FOURCC)),
        )
        self._warn_on_mismatch(info)
        return info

    def _open_http(self) -> CameraInfo:
        capture = HttpMjpegCapture(str(self.source))
        capture.start()
        try:
            frame = capture.read(timeout=5.0)
        except Exception:
            capture.release()
            raise
        if frame is None:
            capture.release()
            raise CameraError(
                f"5 秒内没收到 HTTP 相机帧（{self.source}），确认桥接服务在运行（tools/yirt2_mjpeg_bridge.py）。"
            )
        self._mjpeg = capture
        height, width = frame.shape[:2]
        info = CameraInfo(
            source=str(self.source),
            width=width,
            height=height,
            fps=float(self.fps),
            fourcc="JPEG",
            backend="http",
        )
        if (width, height) != (self.width, self.height):
            log.warning(
                "HTTP 流实际分辨率 %dx%d 与配置 %dx%d 不符，继续运行",
                width, height, self.width, self.height,
            )
        return info

    def _warn_on_mismatch(self, info: CameraInfo) -> None:
        if (info.width, info.height) != (self.width, self.height):
            log.warning(
                "实际分辨率 %dx%d 与配置 %dx%d 不符，继续运行",
                info.width, info.height, self.width, self.height,
            )
        if info.fourcc and info.fourcc != self.fourcc:
            log.warning("实际格式 %s 与配置 %s 不符，帧率可能达不到预期", info.fourcc, self.fourcc)
        if not info.fourcc:
            log.warning("读不到像素格式，可能是驱动未正确上报，先看画面是否正常")
        if info.fps > 0 and self.fps > 0 and abs(info.fps - self.fps) > max(1.0, self.fps * 0.2):
            log.warning(
                "实际帧率 %.0f 与配置 %d 不符，推流带宽会随之变化",
                info.fps, self.fps,
            )

    def read(self):
        if self._mjpeg is not None:
            return self._mjpeg.read()
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame

    def close(self) -> None:
        if self._mjpeg is not None:
            self._mjpeg.release()
            self._mjpeg = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
