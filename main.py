from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from alerts import Alerts
from camera import Camera, CameraError, CameraInfo, list_sources
from coco_labels import COCO_ZH
from detector import DetectorError, DwellEvent, PyDetector
from stream import LocalStream
from tts import TTS

BASE_DIR = Path(__file__).resolve().parent
BOUNDARY = "frame"
CONFIG_PATH = BASE_DIR / "config.yaml"
CHANNELS = ("device", "web", "mute")
DEFAULT_CHANNEL = "device"


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


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
        if backend != "uvc":
            raise CameraError(f"暂不支持 backend={backend}，一期只有 uvc（PRD F-47）")
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

    data_dir = BASE_DIR / "data"
    tts_cfg = config.get("tts", {})
    tts = TTS(tts_cfg, data_dir / "audio")
    alerts = Alerts(data_dir)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "runtime", None) is None:
        app.state.runtime = start_runtime(app.state.config)
    runtime: Runtime = app.state.runtime
    runtime._loop = asyncio.get_running_loop()
    try:
        yield
    finally:
        runtime.stop()


app = FastAPI(title="EyeSentry", lifespan=lifespan)
app.state.config = load_config(CONFIG_PATH) if CONFIG_PATH.exists() else {}
app.state.runtime = None


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/status")
def status(request: Request):
    runtime: Runtime = request.app.state.runtime
    detector = runtime.detector
    det_summary = {"backend": "none"}
    if detector is not None:
        det_summary = detector.summary()
        det_summary["class_options"] = [
            {"id": index, "name": name} for index, name in enumerate(COCO_ZH)
        ]
    camera = runtime.info.as_dict()
    camera["default_source"] = str(runtime.default_source)
    camera["is_default"] = str(runtime.info.source) == str(runtime.default_source)
    return JSONResponse(
        {
            "camera": camera,
            "stream": runtime.stream.stats(),
            "detector": det_summary,
            "channel": runtime.channel,
            "alerts": len(runtime.alerts.list(limit=200)),
        }
    )


@app.get("/api/cameras")
def list_cameras(request: Request):
    runtime: Runtime = request.app.state.runtime
    items = list_sources(active_source=runtime.info.source)
    return JSONResponse(
        {
            "default_source": str(runtime.default_source),
            "current": {"backend": runtime.info.backend, "source": runtime.info.source},
            "items": items,
        }
    )


class CameraSwitchBody(BaseModel):
    backend: str = "uvc"
    source: int | str


@app.post("/api/camera")
def switch_camera(request: Request, body: CameraSwitchBody):
    runtime: Runtime = request.app.state.runtime
    try:
        info = runtime.switch_camera(body.backend, body.source)
    except CameraError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    return JSONResponse(
        {
            "camera": info.as_dict(),
            "default_source": str(runtime.default_source),
            "ok": True,
        }
    )


@app.get("/api/snapshot.jpg")
def snapshot(request: Request, raw: bool = False):
    runtime: Runtime = request.app.state.runtime
    frame = runtime.stream.latest()
    if frame is None:
        return JSONResponse({"detail": "还没有画面"}, status_code=503)
    image = runtime.stream.encode_jpeg(frame.raw) if raw else frame.jpeg
    if image is None:
        return JSONResponse({"detail": "JPEG 编码失败"}, status_code=503)
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Seq": str(frame.seq),
            "X-Frame-Age-Ms": str(int((time.time() - frame.ts) * 1000)),
        },
    )


@app.get("/api/state")
def live_state(request: Request):
    runtime: Runtime = request.app.state.runtime
    frame = runtime.stream.latest()
    dwell = {}
    if runtime.detector is not None:
        dwell = {t["tid"]: t["dwell_seconds"] for t in runtime.detector.dwell.state()}
    targets = [
        {
            "tid": box.get("tid"),
            "cls": box.get("cls"),
            "name": box.get("name"),
            "conf": box.get("conf"),
            "xyxy": box.get("xyxy"),
            "dwell_seconds": dwell.get(box.get("tid"), 0.0),
        }
        for box in (frame.boxes if frame else [])
    ]
    return JSONResponse(
        {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "frame_age_s": round(time.time() - frame.ts, 2) if frame else None,
            "frame_seq": frame.seq if frame else None,
            "snapshot_url": "/api/snapshot.jpg" if frame else None,
            "raw_snapshot_url": "/api/snapshot.jpg?raw=1" if frame else None,
            "people": sum(1 for t in targets if t["cls"] == 0),
            "targets": targets,
            "last_alert": runtime.last_alert,
        }
    )


@app.get("/api/alerts")
def list_alerts(request: Request, limit: int = 50):
    runtime: Runtime = request.app.state.runtime
    limit = max(1, min(int(limit), 200))
    return JSONResponse(runtime.alerts.list(limit=limit))


@app.get("/api/alerts/{alert_id}/snapshot.jpg")
def alert_snapshot(request: Request, alert_id: str):
    runtime: Runtime = request.app.state.runtime
    path = runtime.alerts.snapshot_path(alert_id)
    if path is None:
        return JSONResponse({"detail": "截图不存在"}, status_code=404)
    return FileResponse(path, media_type="image/jpeg")


@app.get("/audio/{name}.mp3")
def audio(request: Request, name: str):
    runtime: Runtime = request.app.state.runtime
    if not name or "/" in name or "\\" in name or ".." in name:
        return JSONResponse({"detail": "非法文件名"}, status_code=400)
    path = runtime.tts.audio_dir / f"{name}.mp3"
    if not path.exists():
        return JSONResponse({"detail": "音频不存在"}, status_code=404)
    return FileResponse(path, media_type="audio/mpeg")


@app.post("/api/settings")
async def update_settings(request: Request):
    runtime: Runtime = request.app.state.runtime
    body = await request.json()

    detector = runtime.detector
    if "detect" in body:
        if detector is None:
            return JSONResponse(
                {"detail": "检测器未启动（detector.backend: none），无法开关检测"},
                status_code=400,
            )
        if not isinstance(body["detect"], bool):
            return JSONResponse({"detail": "detect 必须是布尔值"}, status_code=400)

    # 播报通道：取值校验在改任何状态之前，非法时整单不生效
    channel = body.get("channel")
    if channel is not None and channel not in CHANNELS:
        return JSONResponse(
            {"detail": f"channel 只能是 {' / '.join(CHANNELS)}"},
            status_code=400,
        )

    if detector is not None:
        if "classes" in body:
            raw = body["classes"] or []
            try:
                classes = sorted({int(c) for c in raw if 0 <= int(c) < len(COCO_ZH)})
            except (TypeError, ValueError):
                return JSONResponse({"detail": "classes 必须是类别编号数组"}, status_code=400)
            detector.set_classes(classes)
        if "conf" in body:
            try:
                conf = float(body["conf"])
            except (TypeError, ValueError):
                return JSONResponse({"detail": "conf 必须是数字"}, status_code=400)
            detector.set_conf(min(max(conf, 0.01), 0.99))
        if "detect" in body:
            detector.set_enabled(body["detect"])

    if channel is not None:
        runtime.channel = channel

    return JSONResponse(
        {
            "channel": runtime.channel,
            "classes": detector.summary()["classes"] if detector else [],
            "conf": detector.summary()["conf"] if detector else None,
            "enabled": detector.summary()["enabled"] if detector else None,
        }
    )


@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    runtime: Runtime = websocket.app.state.runtime
    await websocket.accept()
    client = runtime.register_client()
    try:
        while True:
            payload = await client.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        runtime.unregister_client(client)
        try:
            await websocket.close()
        except RuntimeError:
            pass


def _mjpeg_chunk(jpeg: bytes) -> bytes:
    return (
        b"--" + BOUNDARY.encode() + b"\r\n"
        b"Content-Type: image/jpeg\r\n"
        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
        + jpeg + b"\r\n"
    )


MJPEG_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"


@app.get("/stream")
def video_stream(request: Request):
    stream: LocalStream = request.app.state.runtime.stream

    def gen():
        for frame in stream.frames():
            yield _mjpeg_chunk(frame.jpeg)

    return StreamingResponse(gen(), media_type=MJPEG_TYPE)


@app.get("/stream/raw")
def raw_video_stream(request: Request):
    stream: LocalStream = request.app.state.runtime.stream

    def gen():
        # 只在有人看时编码，没客户端时这段代码不跑，不额外占 CPU
        for frame in stream.frames():
            jpeg = stream.encode_jpeg(frame.raw)
            if jpeg is not None:
                yield _mjpeg_chunk(jpeg)

    return StreamingResponse(gen(), media_type=MJPEG_TYPE)


def main() -> None:
    parser = argparse.ArgumentParser(description="EyeSentry M2: 相机识别 + 停留播报")
    parser.add_argument("-c", "--config", default=str(CONFIG_PATH))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    config = load_config(Path(args.config))
    app.state.config = config

    # 在 uvicorn 之前打开相机与模型，启动失败时给人话而不是一屏栈
    try:
        app.state.runtime = start_runtime(config)
    except (CameraError, DetectorError) as exc:
        raise SystemExit(f"启动失败：{exc}") from exc

    server_cfg = config.get("server", {})
    import uvicorn

    try:
        uvicorn.run(
            app,
            host=str(server_cfg.get("host", "0.0.0.0")),
            port=int(server_cfg.get("port", 8000)),
            log_level="info",
        )
    finally:
        app.state.runtime.stop()


if __name__ == "__main__":
    main()
