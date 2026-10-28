"""把 cockpit 的相机 topic 桥接成本地 MJPEG。

直接用 cockpit 自带的 yirt2 Python 接口订阅 `/cockpit/camera/chest/main/color`，
转成 JPEG 后在本机暴露 MJPEG，供 EyeSentry 的 `camera.source` 使用。

为什么不用标准 rclpy：cockpit 的 YiRT2/FastDDS 走 128MB 共享内存段，且
RouDi 的 IPC socket `/tmp/roudi` 是 root:root 0660。普通用户即使能发现 topic、
订阅也能匹配，但拿不到数据。本工具需要 root 运行。

相比 cockpit Web 预览：不占用控制台独占会话，也不会被网页点同一路预览顶掉。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import yirt2

log = logging.getLogger("yirt2-bridge")

BOUNDARY = "frame"
DEFAULT_TOPIC = "/cockpit/camera/chest/main/color"


class Frames:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self.seq = 0
        self.jpeg: bytes | None = None
        self.ready = threading.Event()

    def publish(self, jpeg: bytes) -> None:
        with self._cond:
            self.jpeg = jpeg
            self.seq += 1
            self._cond.notify_all()
        self.ready.set()

    def latest(self):
        with self._cond:
            return self.seq, self.jpeg

    def wait(self, after: int, timeout: float):
        with self._cond:
            if self.seq == after:
                self._cond.wait(timeout)
            return self.seq, self.jpeg


def to_bgr(image) -> np.ndarray | None:
    info = image.info
    width, height, step = info.width, info.height, info.step
    encoding = info.encoding
    buf = np.frombuffer(image.data, dtype=np.uint8)
    if width <= 0 or height <= 0 or buf.size == 0:
        return None
    if encoding in ("rgb8", "bgr8"):
        row = width * 3
        if step < row or buf.size < step * height:
            return None
        pixels = buf[: step * height].reshape(height, step)[:, :row].reshape(height, width, 3)
        return pixels if encoding == "bgr8" else cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
    if encoding == "mono8":
        if buf.size < step * height:
            return None
        gray = buf[: step * height].reshape(height, step)[:, :width]
        return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    if encoding == "nv12":
        need = width * height * 3 // 2
        if buf.size < need:
            return None
        return cv2.cvtColor(
            buf[:need].reshape(height * 3 // 2, width), cv2.COLOR_YUV2BGR_NV12
        )
    if encoding in ("yuv422_yuy2", "yuyv"):
        need = width * height * 2
        if buf.size < need:
            return None
        return cv2.cvtColor(
            buf[:need].reshape(height, width, 2), cv2.COLOR_YUV2BGR_YUY2
        )
    log.warning("暂不支持的图像编码 %s", encoding)
    return None


class Source:
    """订阅并编码；超过 stale_seconds 无帧就重订阅。"""

    def __init__(self, topic: str, fps: float, quality: int, stale_seconds: float):
        self.topic = topic
        self.stale_seconds = stale_seconds
        self.min_interval = 1.0 / fps if fps > 0 else 0.0
        self.quality = [cv2.IMWRITE_JPEG_QUALITY, quality]
        self.frames = Frames()
        self.state = "starting"
        self.fps = 0.0
        self._stop = threading.Event()
        self._last_frame = 0.0
        self._raw = None
        self._raw_seq = 0
        self._raw_cond = threading.Condition()
        self._encode_count = 0
        self._uploaded = 0
        self._thread = threading.Thread(target=self._run, name="yirt2-sub", daemon=True)
        self._encoder = threading.Thread(target=self._encode_loop, name="jpeg-enc", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._encoder.start()

    def stop(self) -> None:
        self._stop.set()
        self.frames.ready.set()
        with self.frames._cond:
            self.frames._cond.notify_all()
        with self._raw_cond:
            self._raw_cond.notify_all()

    def _on_image(self, image) -> None:
        self._last_frame = time.monotonic()
        self._uploaded += 1
        with self._raw_cond:
            self._raw = image
            self._raw_seq += 1
            self._raw_cond.notify_all()

    def _encode_loop(self) -> None:
        """按目标帧率取最新一帧编码，和相机 30 fps 解耦。"""
        interval = self.min_interval
        seen = 0
        next_at = time.monotonic()
        while not self._stop.is_set():
            with self._raw_cond:
                if self._raw_seq == seen:
                    self._raw_cond.wait(interval if interval else 1.0)
                    continue
                image = self._raw
                seen = self._raw_seq
            bgr = to_bgr(image)
            if bgr is not None:
                ok, encoded = cv2.imencode(".jpg", bgr, self.quality)
                if ok:
                    self._encode_count += 1
                    self.frames.publish(encoded.tobytes())
            if interval:
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_at = time.monotonic()

    def _run(self) -> None:
        options = yirt2.ImageSubscriberOptions()
        while not self._stop.is_set():
            self.state = "subscribing"
            sub = yirt2.ImageSubscriber()
            try:
                ok = sub.init(self._on_image, self.topic, "/", options)
            except Exception as exc:
                log.error("订阅 %s 失败：%s", self.topic, exc)
                self.state = "error"
                self._stop.wait(3.0)
                continue
            if not ok:
                log.error("订阅 %s 返回失败", self.topic)
                self.state = "error"
                self._stop.wait(3.0)
                continue
            self.state = "streaming"
            self._last_frame = time.monotonic()
            log.info("已订阅 %s", self.topic)
            while not self._stop.is_set():
                if time.monotonic() - self._last_frame > self.stale_seconds:
                    log.warning("超过 %.0f 秒无帧，重订阅", self.stale_seconds)
                    break
                self._stop.wait(0.5)
            try:
                sub.shutdown()
            except Exception:
                pass
            if not self._stop.is_set():
                self._stop.wait(1.0)

    def metrics(self) -> dict:
        now = time.monotonic()
        return {
            "state": self.state,
            "topic": self.topic,
            "frames": self.frames.seq,
            "received": self._uploaded,
            "encoded": self._encode_count,
            "last_frame_bytes": len(self.frames.jpeg or b""),
            "last_frame_age_s": round(now - self._last_frame, 3) if self._last_frame else None,
        }


class Handler(BaseHTTPRequestHandler):
    source: Source = None

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/status":
            body = json.dumps(self.source.metrics(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/stream", "/snapshot.jpg"):
            self._serve(snapshot=path.endswith(".jpg"))
            return
        self.send_error(404)

    def _serve(self, snapshot: bool) -> None:
        if snapshot:
            _, jpeg = self.source.frames.latest()
            if not jpeg:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        seq = 0
        try:
            while not self.source._stop.is_set():
                new_seq, jpeg = self.source.frames.wait(seq, 2.0)
                if jpeg is None or new_seq == seq:
                    continue
                seq = new_seq
                self.wfile.write(
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                )
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--fps", type=float, default=15.0, help="编码上限（相机 30 fps）")
    parser.add_argument("--quality", type=int, default=80)
    parser.add_argument("--stale-seconds", type=float, default=10.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        raise SystemExit(
            "需要 root：cockpit 的 RouDi IPC socket 为 root:root 0660，"
            "普通用户订阅会匹配但收不到帧。用 sudo 以 root 运行。"
        )

    source = Source(args.topic, args.fps, args.quality, args.stale_seconds)
    Handler.source = source
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    source.start()

    def stop(signum, frame):
        log.info("收到信号 %s，退出", signum)
        source.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("本地 MJPEG：http://%s:%d/stream（topic=%s）", args.host, args.port, args.topic)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        source.stop()
        server.server_close()


if __name__ == "__main__":
    main()
