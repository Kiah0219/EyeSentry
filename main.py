"""EyeSentry 入口：解析参数、启动运行时、交给 uvicorn。"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from camera import CameraError
from detector import DetectorError
from runtime import start_runtime
from settings import CONFIG_PATH, load_config
from web import app


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
            # 浏览器挂着 /stream 时 SIGTERM 会一直等后台任务结束，进程停不掉
            timeout_graceful_shutdown=5,
        )
    finally:
        app.state.runtime.stop()


if __name__ == "__main__":
    main()
