"""告警留痕：一条事件 = 一条记录 + 一张带框截图（PRD F-34~F-37）。

存储只用一个 JSONL 加一个截图目录，不引数据库——50 条记录的量级，SQLite 是过度设计。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

import cv2

log = logging.getLogger(__name__)

MAX_SNAPSHOTS = 200
SNAPSHOT_QUALITY = 85


class Alerts:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.snapshot_dir = self.data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.data_dir / "alerts.jsonl"
        self._lock = threading.Lock()
        self._counter = self._count_existing()

    def _count_existing(self) -> int:
        if not self.jsonl.exists():
            return 0
        with self.jsonl.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def reserve_id(self) -> str:
        """先占一个 ID，音频与截图都以它命名，再写记录。"""
        now = datetime.now().astimezone()
        with self._lock:
            self._counter += 1
            return f"{now:%Y%m%d-%H%M%S}-{self._counter}"

    def create(self, alert_id: str, frame, people: int, dwell_seconds: float, text: str, audio_url: str | None) -> dict:
        """frame 是带框的 BGR 图；people/dwell 来自 DwellEvent。"""
        now = datetime.now().astimezone()
        with self._lock:
            snapshot = self.snapshot_dir / f"{alert_id}.jpg"
            record = {
                "id": alert_id,
                "ts": now.isoformat(timespec="seconds"),
                "people": people,
                "dwell_seconds": dwell_seconds,
                "text": text,
                "audio_url": audio_url,
                "snapshot_url": f"/api/alerts/{alert_id}/snapshot.jpg",
            }
            ok, buf = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), SNAPSHOT_QUALITY]
            )
            if ok:
                snapshot.write_bytes(buf.tobytes())
            else:
                log.warning("告警截图编码失败，本条记录无图")
                record["snapshot_url"] = None

            with self.jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._prune_snapshots()

        log.info("告警 %s：%d 人，%.1fs", alert_id, people, dwell_seconds)
        return record

    def list(self, limit: int = 50) -> list[dict]:
        if not self.jsonl.exists():
            return []
        with self._lock:
            lines = self.jsonl.read_text(encoding="utf-8").splitlines()
        records = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(records) >= limit:
                break
        return records

    def snapshot_path(self, alert_id: str) -> Path | None:
        # 只接受自家 ID，避免路径穿越
        if not alert_id or "/" in alert_id or "\\" in alert_id or ".." in alert_id:
            return None
        path = self.snapshot_dir / f"{alert_id}.jpg"
        return path if path.exists() else None

    def _prune_snapshots(self) -> None:
        files = sorted(
            self.snapshot_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for stale in files[MAX_SNAPSHOTS:]:
            try:
                stale.unlink()
            except OSError:
                pass
