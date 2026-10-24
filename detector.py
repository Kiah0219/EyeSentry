"""检测：YOLO 推理与跟踪、停留判定、画框、中文标签。

对外两个入口：
  process(frame) -> (annotated, boxes)   推理 + 画框
  update_dwell(boxes, now) -> [事件]      停留判定

boxes 的字段就是二期 C++ 侧车的跨进程协议字段（PRD 7.8），另有 tid——跟踪
留在 Python，所以 tid 由本文件补上，C++ 不产出它。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coco_labels import name_of

log = logging.getLogger(__name__)

PERSON_CLS = 0

# 标签用 RGB，画到 PIL 图上
_PALETTE = (
    (255, 99, 71), (64, 200, 120), (70, 150, 255), (255, 190, 60),
    (200, 120, 255), (90, 220, 220), (255, 130, 190), (140, 200, 90),
)

_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


class DetectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Detection:
    cls: int
    name: str
    conf: float
    xyxy: tuple
    tid: int | None = None

    def as_dict(self) -> dict:
        return {
            "cls": self.cls,
            "name": self.name,
            "conf": round(self.conf, 3),
            "xyxy": [int(v) for v in self.xyxy],
            "tid": self.tid,
        }


@dataclass(frozen=True)
class DwellEvent:
    """一次停留事件：同一帧内所有满足条件的目标合并成一条（F-22）。"""

    people: int
    dwell_seconds: float
    tids: tuple = field(default_factory=tuple)


class DwellTracker:
    """停留判定（PRD 7.5）。纯时间逻辑，不碰像素，也不依赖跟踪器实现。

    只对带 tid 的目标计时——没有 ID 就无法跨帧确认是「同一个人」。
    """

    def __init__(
        self,
        seconds: float = 5.0,
        cooldown_seconds: float = 30.0,
        lost_tolerance_seconds: float = 2.0,
    ):
        self.seconds = float(seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.lost_tolerance_seconds = float(lost_tolerance_seconds)
        self._first_seen: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}
        self._last_trigger: dict[int, float] = {}
        log.info(
            "停留判定：阈值 %.1fs 冷却 %.0fs 丢失容忍 %.1fs",
            self.seconds, self.cooldown_seconds, self.lost_tolerance_seconds,
        )

    def update(self, boxes, now: float) -> list[DwellEvent]:
        for box in boxes:
            tid = box.get("tid")
            if tid is None:
                continue
            self._last_seen[tid] = now
            self._first_seen.setdefault(tid, now)

        due = []
        for tid, first in self._first_seen.items():
            if now - first < self.seconds:
                continue
            if now - self._last_trigger.get(tid, float("-inf")) < self.cooldown_seconds:
                continue
            due.append(tid)

        for tid in due:
            self._last_trigger[tid] = now

        for tid in list(self._first_seen):
            if now - self._last_seen.get(tid, now) > self.lost_tolerance_seconds:
                del self._first_seen[tid]
                self._last_seen.pop(tid, None)

        if not due:
            return []
        return [DwellEvent(people=len(due), dwell_seconds=self.seconds, tids=tuple(due))]

    def seconds_for(self, tid: int | None, now: float) -> float:
        """已停留秒数，用于画面显示（F-23）。"""
        if tid is None or tid not in self._first_seen:
            return 0.0
        return now - self._first_seen[tid]


def _find_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _ensure_model(model_path: Path) -> None:
    if model_path.exists():
        return
    model_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("模型 %s 不存在，尝试下载（约 6 MB）", model_path.name)
    try:
        from ultralytics.utils.downloads import attempt_download_asset

        attempt_download_asset(str(model_path))
    except Exception as exc:
        raise DetectorError(
            f"下载模型失败：{exc}。可手动下载 {model_path.name} 放到 {model_path.parent}/"
        ) from exc
    if not model_path.exists():
        raise DetectorError(f"模型未就绪，请把 {model_path.name} 放到 {model_path.parent}/")


class PyDetector:
    def __init__(
        self,
        model_path,
        conf: float = 0.4,
        imgsz: int = 640,
        classes=None,
        dwell: dict | None = None,
    ):
        model_path = Path(model_path)
        _ensure_model(model_path)
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise DetectorError("未安装 ultralytics，执行 pip install -r requirements.txt") from exc

        self._model = YOLO(str(model_path))
        self._conf = conf
        self._imgsz = imgsz
        self._classes = [int(c) for c in classes] if classes else None

        dwell = dwell or {}
        self.dwell = DwellTracker(
            seconds=dwell.get("seconds", 5.0),
            cooldown_seconds=dwell.get("cooldown_seconds", 30.0),
            lost_tolerance_seconds=dwell.get("lost_tolerance_seconds", 2.0),
        )

        self._font_path = _find_font()
        if self._font_path is None:
            log.warning("找不到中文字体，标签将显示英文。可在系统装上 Noto CJK 后重启")
        self._font_cache = {}
        self.model_name = model_path.name
        log.info(
            "检测器就绪：%s conf=%.2f imgsz=%d 白名单=%s",
            model_path.name, conf, imgsz, self._classes or "全部",
        )

    def summary(self) -> dict:
        return {
            "backend": "python",
            "model": self.model_name,
            "conf": self._conf,
            "imgsz": self._imgsz,
            "classes": self._classes,
            "labels": "chinese" if self._font_path else "english",
            "tracker": "bytetrack",
            "dwell_seconds": self.dwell.seconds,
        }

    def process(self, frame):
        result = self._model.track(
            frame,
            conf=self._conf,
            imgsz=self._imgsz,
            classes=self._classes,
            persist=True,
            tracker="bytetrack.yaml",
            verbose=False,
        )[0]

        ids = result.boxes.id
        boxes = []
        for index, box in enumerate(result.boxes):
            cls_id = int(box.cls.item())
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            boxes.append(
                Detection(
                    cls=cls_id,
                    name=name_of(cls_id, chinese=self._font_path is not None),
                    conf=float(box.conf.item()),
                    xyxy=(x1, y1, x2, y2),
                    tid=int(ids[index].item()) if ids is not None else None,
                )
            )

        return self._draw(frame, boxes), [b.as_dict() for b in boxes]

    def update_dwell(self, boxes, now: float | None = None) -> list[DwellEvent]:
        return self.dwell.update(boxes, now if now is not None else time.monotonic())

    def set_classes(self, classes) -> list[int]:
        """热改白名单（F-16）。下一帧生效，不需要重启。"""
        self._classes = [int(c) for c in classes] if classes else None
        log.info("白名单已更新：%s", self._classes or "全部")
        return self._classes or []

    def set_conf(self, conf: float) -> float:
        self._conf = float(conf)
        log.info("置信度阈值已更新：%.2f", self._conf)
        return self._conf

    def _font(self, size: int):
        if self._font_path is None:
            return None
        font = self._font_cache.get(size)
        if font is None:
            try:
                font = ImageFont.truetype(self._font_path, size)
            except OSError:
                font = ImageFont.truetype(self._font_path, size, index=0)
            self._font_cache[size] = font
        return font

    def _label(self, det: Detection, now: float) -> str:
        text = f"{det.name} {det.conf:.2f}"
        if det.cls == PERSON_CLS and det.tid is not None:
            seconds = self.dwell.seconds_for(det.tid, now)
            if seconds >= 0.5:
                text += f" · {seconds:.1f}s"
        return text

    def _draw(self, frame, detections):
        if not detections:
            return frame

        if self._font_path is None:
            return self._draw_cv(frame, detections)

        now = time.monotonic()
        image = Image.fromarray(frame[:, :, ::-1])
        draw = ImageDraw.Draw(image)
        height = frame.shape[0]
        font = self._font(max(14, height // 40))
        thickness = max(2, height // 360)

        for det in detections:
            x1, y1, x2, y2 = det.xyxy
            color = _PALETTE[det.cls % len(_PALETTE)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=thickness)

            text = self._label(det, now)
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
            box_h = bottom - top
            box_w = right - left
            ty = y1 - box_h - 8
            if ty < 0:
                ty = y1 + 2
            draw.rectangle([x1, ty, x1 + box_w + 10, ty + box_h + 8], fill=color)
            draw.text((x1 + 5, ty + 4 - top), text, font=font, fill=(16, 16, 16))

        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    def _draw_cv(self, frame, detections):
        now = time.monotonic()
        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det.xyxy)
            color = _PALETTE[det.cls % len(_PALETTE)]
            color_bgr = (color[2], color[1], color[0])
            cv2.rectangle(frame, (x1, y1), (x2, y2), color_bgr, 2)
            text = self._label(det, now)
            cv2.putText(
                frame, text, (x1 + 4, max(16, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_bgr, 2, cv2.LINE_AA,
            )
        return frame
