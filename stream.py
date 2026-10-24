"""帧流：把相机读帧与后续的推理封成一个产出固定形状的生成器。

上层只认 `for jpeg, boxes in stream`；一期 LocalStream、二期 SidecarStream 产出同构，
切换 C++ 侧车时上层代码不动（见 PRD 7.8）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import cv2

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    boxes: list
    seq: int
    ts: float
    width: int
    height: int


class LocalStream(threading.Thread):
    def __init__(self, camera, jpeg_quality: int = 80, fps_window: int = 30):
        super().__init__(name="local-stream", daemon=True)
        self._camera = camera
        self._jpeg_quality = jpeg_quality
        self._fps_window = fps_window
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._timestamps = []
        self._read_failures = 0

    def run(self) -> None:
        for jpeg, boxes, width, height in self._produce():
            now = time.time()
            with self._cond:
                self._seq += 1
                self._frame = Frame(
                    jpeg=jpeg, boxes=boxes, seq=self._seq, ts=now,
                    width=width, height=height,
                )
                self._timestamps.append(now)
                del self._timestamps[:-self._fps_window]
                self._cond.notify_all()

    def _produce(self):
        # M1 起在这里插入 detector.process(frame)，产出形状保持不变
        while not self._stop.is_set():
            frame = self._camera.read()
            if frame is None:
                self._read_failures += 1
                if self._read_failures in (1, 10, 100) or self._read_failures % 500 == 0:
                    log.warning("读帧连续失败 %d 次，检查相机连接", self._read_failures)
                time.sleep(0.05)
                continue
            if self._read_failures:
                log.info("读帧恢复，此前连续失败 %d 次", self._read_failures)
                self._read_failures = 0

            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
            if not ok:
                log.warning("JPEG 编码失败，跳过该帧")
                continue
            height, width = frame.shape[:2]
            yield buf.tobytes(), [], width, height

    def frames(self):
        last = -1
        while not self._stop.is_set():
            with self._cond:
                self._cond.wait_for(lambda: self._seq != last, timeout=1.0)
                if self._seq == last:
                    continue
                last = self._seq
                frame = self._frame
            yield frame

    def stats(self) -> dict:
        with self._cond:
            fps = 0.0
            if len(self._timestamps) >= 2:
                span = self._timestamps[-1] - self._timestamps[0]
                if span > 0:
                    fps = (len(self._timestamps) - 1) / span
            frame = self._frame
            return {
                "connected": frame is not None,
                "fps": round(fps, 1),
                "seq": self._seq,
                "width": frame.width if frame else 0,
                "height": frame.height if frame else 0,
                "boxes": len(frame.boxes) if frame else 0,
                "last_frame_age_s": round(time.time() - frame.ts, 2) if frame else None,
            }

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
