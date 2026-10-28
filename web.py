"""HTTP / WebSocket 层。

只通过 Runtime 暴露的操作访问业务；流式接口与页面资源都在这里，
换成 C++ 侧车时本文件不需要改。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from camera import CameraError, list_sources
from coco_labels import COCO_ZH
from runtime import Runtime, start_runtime
from settings import BASE_DIR, CHANNELS, CONFIG_PATH, load_config

BOUNDARY = "frame"
MJPEG_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"


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
    items = list_sources(
        active_source=runtime.info.source,
        default_source=runtime.default_source,
    )
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


@app.get("/stream")
def video_stream(request: Request):
    stream = request.app.state.runtime.stream

    def gen():
        for frame in stream.frames():
            yield _mjpeg_chunk(frame.jpeg)

    return StreamingResponse(gen(), media_type=MJPEG_TYPE)


@app.get("/stream/raw")
def raw_video_stream(request: Request):
    stream = request.app.state.runtime.stream

    def gen():
        # 只在有人看时编码，没客户端时这段代码不跑，不额外占 CPU
        for frame in stream.frames():
            jpeg = stream.encode_jpeg(frame.raw)
            if jpeg is not None:
                yield _mjpeg_chunk(jpeg)

    return StreamingResponse(gen(), media_type=MJPEG_TYPE)
