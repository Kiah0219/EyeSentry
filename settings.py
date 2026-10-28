"""路径、全局常量与配置加载。"""

from __future__ import annotations

from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DATA_DIR = BASE_DIR / "data"
CHANNELS = ("device", "web", "mute")
DEFAULT_CHANNEL = "device"


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
