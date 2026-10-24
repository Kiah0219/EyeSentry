"""相机采集：打开、读帧、格式自检。

一台 RealSense 会占 5~6 个 /dev/videoN（深度 / IR / 彩色 / 元数据），
只有同时支持 YUYV 与 MJPG 的那个是彩色流；用 tools/check_camera.py 找。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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

    def as_dict(self) -> dict:
        return {
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


def _decode_fourcc(value: float) -> str:
    code = int(value)
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00")


class Camera:
    def __init__(self, source, width=1280, height=720, fps=15, fourcc="MJPG"):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.fourcc = fourcc
        self._cap = None

    def open(self) -> CameraInfo:
        src, backend = _parse_source(self.source)
        cap = cv2.VideoCapture(src, backend)
        if not cap.isOpened():
            raise CameraError(
                f"打不开相机源 {self.source!r}。先运行 python tools/check_camera.py 看可用设备。"
            )

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.fourcc))
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

    def read(self):
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
