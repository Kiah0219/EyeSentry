from __future__ import annotations

import argparse
import asyncio
import logging
import queue
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from alerts import Alerts
from camera import Camera, CameraError, CameraInfo
from coco_labels import COCO_ZH
from detector import DetectorError, DwellEvent, PyDetector
from stream import LocalStream
from tts import TTS

BASE_DIR = Path(__file__).resolve().parent
BOUNDARY = "frame"
CONFIG_PATH = BASE_DIR / "config.yaml"


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Runtime:
    camera: Camera
    info: CameraInfo
    stream: LocalStream
    detector: PyDetector | None
    tts: TTS
    alerts: Alerts
    channels: dict = field(default_factory=lambda: {"web": True, "device": True})
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=16))
    _clients: set = field(default_factory=set)
    _loop: asyncio.AbstractEventLoop | None = None
    _worker: threading.Thread | None = None

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
        if self.channels["web"] or self.channels["device"]:
            # F-26：一次触发只合成一次，两个通道共用同一个文件
            audio = self.tts.synthesize(text, alert_id)

        if self.channels["device"]:
            if audio is not None:
                self.tts.play_on_device(audio)
            else:
                self.tts.beep()  # F-32：合成失败时本体播提示音

        audio_url = audio.name if audio is not None else None
        record = self.alerts.create(
            alert_id, frame, event.people, event.dwell_seconds, text,
            f"/audio/{audio_url}" if audio_url else None,
        )
        self.broadcast({"type": "alert", "alert": record, "play_web": self.channels["web"]})

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
    camera = Camera(
        source=cam_cfg.get("source", 0),
        width=int(cam_cfg.get("width", 1280)),
        height=int(cam_cfg.get("height", 720)),
        fps=int(cam_cfg.get("fps", 15)),
        fourcc=str(cam_cfg.get("fourcc", "MJPG")),
    )
    info = camera.open()
    logging.info("相机已打开：%s", info.as_dict())

    detector = build_detector(config)

    data_dir = BASE_DIR / "data"
    tts_cfg = config.get("tts", {})
    tts = TTS(tts_cfg, data_dir / "audio")
    alerts = Alerts(data_dir)

    channels = tts_cfg.get("channels", {})
    runtime = Runtime(
        camera=camera,
        info=info,
        stream=None,  # 下面创建，需要把 runtime 的回调挂上去
        detector=detector,
        tts=tts,
        alerts=alerts,
        channels={
            "web": bool(channels.get("web", True)),
            "device": bool(channels.get("device", True)),
        },
    )
    runtime.stream = LocalStream(
        camera,
        detector=detector,
        jpeg_quality=int(config.get("stream", {}).get("jpeg_quality", 80)),
        target_fps=float(cam_cfg.get("fps", 15)),
        on_event=runtime.on_event,
    )
    runtime.stream.start()
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
    return JSONResponse(
        {
            "camera": runtime.info.as_dict(),
            "stream": runtime.stream.stats(),
            "detector": det_summary,
            "channels": runtime.channels,
            "alerts": len(runtime.alerts.list(limit=200)),
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

    channels = body.get("channels") or {}
    for key in ("web", "device"):
        if key in channels:
            runtime.channels[key] = bool(channels[key])

    detector = runtime.detector
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

    return JSONResponse(
        {
            "channels": runtime.channels,
            "classes": detector.summary()["classes"] if detector else [],
            "conf": detector.summary()["conf"] if detector else None,
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


@app.get("/stream")
def video_stream(request: Request):
    stream: LocalStream = request.app.state.runtime.stream

    def gen():
        for frame in stream.frames():
            yield (
                b"--" + BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame.jpeg)).encode() + b"\r\n\r\n"
                + frame.jpeg + b"\r\n"
            )

    return StreamingResponse(
        gen(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}"
    )


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
