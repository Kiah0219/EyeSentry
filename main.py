from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from camera import Camera, CameraError, CameraInfo
from detector import DetectorError, PyDetector
from stream import LocalStream

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
    detector: PyDetector | None = None

    def stop(self) -> None:
        self.stream.stop()
        self.stream.join(timeout=3)
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

    stream = LocalStream(
        camera,
        detector=detector,
        jpeg_quality=int(config.get("stream", {}).get("jpeg_quality", 80)),
        target_fps=float(cam_cfg.get("fps", 15)),
    )
    stream.start()
    return Runtime(camera=camera, info=info, stream=stream, detector=detector)


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
        conf=float(det_cfg.get("conf", 0.35)),
        imgsz=int(det_cfg.get("imgsz", 640)),
        classes=det_cfg.get("classes"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "runtime", None) is None:
        app.state.runtime = start_runtime(app.state.config)
    try:
        yield
    finally:
        app.state.runtime.stop()


app = FastAPI(title="EyeSentry", lifespan=lifespan)
app.state.config = load_config(CONFIG_PATH) if CONFIG_PATH.exists() else {}
app.state.runtime = None


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/status")
def status(request: Request):
    runtime: Runtime = request.app.state.runtime
    return JSONResponse(
        {
            "camera": runtime.info.as_dict(),
            "stream": runtime.stream.stats(),
            "detector": runtime.detector.summary() if runtime.detector else {"backend": "none"},
        }
    )


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
    parser = argparse.ArgumentParser(description="EyeSentry M0: camera to browser")
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
