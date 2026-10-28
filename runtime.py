"""运行时编排：相机、检测、事件流水线。

这一层不依赖 FastAPI；引入 C++ 侧车时，替换的是 `detector` / `stream` 的实现，
这里的调用形状保持不变。
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass, field

from alerts import Alerts
from camera import Camera, CameraError, CameraInfo
from detector import DwellEvent, PyDetector
from settings import BASE_DIR, CHANNELS, DATA_DIR, DEFAULT_CHANNEL
from stream import LocalStream
from tts import TTS


def open_camera(source, cfg: dict) -> tuple[Camera, CameraInfo]:
    camera = Camera(
        source=source,
        width=int(cfg.get("width", 1280)),
        height=int(cfg.get("height", 720)),
        fps=int(cfg.get("fps", 15)),
        fourcc=str(cfg.get("fourcc", "MJPG")),
    )
    return camera, camera.open()


@dataclass
class Runtime:
    camera: Camera
    info: CameraInfo
    stream: LocalStream
    detector: PyDetector | None
    tts: TTS
    alerts: Alerts
    channel: str = DEFAULT_CHANNEL
    last_alert: dict | None = None
    camera_cfg: dict = field(default_factory=dict)
    default_source: object = 0
    jpeg_quality: int = 80
    target_fps: float = 15.0
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=16))
    _clients: set = field(default_factory=set)
    _loop: asyncio.AbstractEventLoop | None = None
    _worker: threading.Thread | None = None
    _switch_lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- 相机热切换（F-06）：停流 → 关旧设备 → 开新设备 → 重启流 ----

    def _start_stream(self) -> None:
        self.stream = LocalStream(
            self.camera,
            detector=self.detector,
            jpeg_quality=self.jpeg_quality,
            target_fps=self.target_fps,
            on_event=self.on_event,
        )
        self.stream.start()

    def switch_camera(self, backend: str, source) -> CameraInfo:
        if backend not in ("uvc", "http"):
            raise CameraError(f"暂不支持 backend={backend}，可选 uvc 或 http（PRD F-47）")
        with self._switch_lock:
            old_info = self.info
            old_stream = self.stream
            old_stream.stop()
            old_stream.join(timeout=3)
            self.camera.close()
            try:
                camera, info = open_camera(source, self.camera_cfg)
            except Exception as exc:
                recovery = ""
                try:
                    self.camera, self.info = open_camera(old_info.source, self.camera_cfg)
                    self._start_stream()
                except Exception as again:
                    recovery = f"；原相机也未能重新打开（{again}），请重启服务"
                raise CameraError(f"切换失败：{exc}{recovery}") from exc
            self.camera, self.info = camera, info
            if self.detector is not None:
                self.detector.reset_tracking()
            self._start_stream()
            logging.info("已切换相机：%s", info.as_dict())
            return info

    # ---- 事件流水线：采集线程只入队，这里做合成、播报、落库、广播 ----

    def on_event(self, event: DwellEvent, frame) -> None:
        try:
            self.events.put_nowait((event, frame.copy()))
        except queue.Full:
            logging.warning("事件队列已满，丢弃本次事件")

    def start_worker(self) -> None:
        self._worker = threading.Thread(target=self._event_loop, name="event-worker", daemon=True)
        self._worker.start()

    def _event_loop(self) -> None:
        while True:
            item = self.events.get()
            if item is None:
                return
            event, frame = item
            try:
                self._handle_event(event, frame)
            except Exception:
                logging.exception("处理停留事件失败")

    def _handle_event(self, event: DwellEvent, frame) -> None:
        alert_id = self.alerts.reserve_id()
        text = self.tts.text_for(event.people, event.dwell_seconds)

        audio = None
        if self.channel != "mute":
            # F-26：一次触发只合成一次
            audio = self.tts.synthesize(text, alert_id)

        if self.channel == "device":
            if audio is not None:
                self.tts.play_on_device(audio)
            else:
                self.tts.beep()  # F-32：合成失败时本体播提示音

        audio_url = audio.name if audio is not None else None
        record = self.alerts.create(
            alert_id, frame, event.people, event.dwell_seconds, text,
            f"/audio/{audio_url}" if audio_url else None,
        )
        self.last_alert = record
        self.broadcast({"type": "alert", "alert": record, "play_web": self.channel == "web"})

    # ---- WebSocket 广播：线程安全地投递给每个连接的队列 ----

    def register_client(self) -> asyncio.Queue:
        client: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._clients.add(client)
        return client

    def unregister_client(self, client: asyncio.Queue) -> None:
        self._clients.discard(client)

    def broadcast(self, payload: dict) -> None:
        if self._loop is None:
            return

        def push() -> None:
            for client in list(self._clients):
                try:
                    client.put_nowait(payload)
                except asyncio.QueueFull:
                    pass

        self._loop.call_soon_threadsafe(push)

    def stop(self) -> None:
        self.stream.stop()
        self.stream.join(timeout=3)
        try:
            self.events.put_nowait(None)
        except queue.Full:
            pass
        self.tts.close()
        self.camera.close()


def start_runtime(config: dict) -> Runtime:
    cam_cfg = config.get("camera", {})
    default_source = cam_cfg.get("source", 0)
    camera, info = open_camera(default_source, cam_cfg)
    logging.info("相机已打开：%s", info.as_dict())

    detector = build_detector(config)

    tts_cfg = config.get("tts", {})
    tts = TTS(tts_cfg, DATA_DIR / "audio")
    alerts = Alerts(DATA_DIR)

    channel = tts_cfg.get("channel", DEFAULT_CHANNEL)
    if channel not in CHANNELS:
        raise ValueError(
            f"tts.channel 只能是 {' / '.join(CHANNELS)}，当前为 {channel!r}"
        )
    runtime = Runtime(
        camera=camera,
        info=info,
        stream=None,  # 下面创建，需要把 runtime 的回调挂上去
        detector=detector,
        tts=tts,
        alerts=alerts,
        channel=channel,
        # 回填最近一条落盘告警，重启后 /api/state 立刻有内容
        last_alert=(alerts.list(limit=1) or [None])[0],
        camera_cfg=dict(cam_cfg),
        default_source=default_source,
        jpeg_quality=int(config.get("stream", {}).get("jpeg_quality", 80)),
        target_fps=float(cam_cfg.get("fps", 15)),
    )
    runtime._start_stream()
    runtime.start_worker()
    return runtime


def build_detector(config: dict):
    det_cfg = config.get("detector", {})
    backend = str(det_cfg.get("backend", "python"))
    if backend == "none":
        logging.info("检测已关闭（detector.backend: none）")
        return None
    if backend != "python":
        raise SystemExit(
            f"暂不支持 detector.backend={backend}；C++ 侧车在二期，当前请用 python 或 none"
        )
    return PyDetector(
        model_path=BASE_DIR / str(det_cfg.get("model", "models/yolov8n.pt")),
        conf=float(det_cfg.get("conf", 0.4)),
        imgsz=int(det_cfg.get("imgsz", 640)),
        classes=det_cfg.get("classes"),
        dwell=config.get("dwell", {}),
    )
