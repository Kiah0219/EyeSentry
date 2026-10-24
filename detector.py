"""检测：YOLO 推理、画框、中文标签。

对外只有 process(frame) -> (annotated, boxes)，boxes 的字段就是二期 C++
侧车的跨进程协议字段（PRD 7.8），届时换实现不动上层。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from coco_labels import COCO_EN, name_of

log = logging.getLogger(__name__)

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

    def as_dict(self) -> dict:
        return {
            "cls": self.cls,
            "name": self.name,
            "conf": round(self.conf, 3),
            "xyxy": [int(v) for v in self.xyxy],
        }


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
    def __init__(self, model_path, conf: float = 0.4, imgsz: int = 640, classes=None):
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
        }

    def process(self, frame):
        result = self._model.predict(
            frame,
            conf=self._conf,
            imgsz=self._imgsz,
            classes=self._classes,
            verbose=False,
        )[0]

        boxes = []
        for box in result.boxes:
            cls_id = int(box.cls.item())
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            boxes.append(
                Detection(
                    cls=cls_id,
                    name=name_of(cls_id, chinese=self._font_path is not None),
                    conf=float(box.conf.item()),
                    xyxy=(x1, y1, x2, y2),
                )
            )

        return self._draw(frame, boxes), [b.as_dict() for b in boxes]

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

    def _draw(self, frame, detections):
        if not detections:
            return frame

        if self._font_path is None:
            return self._draw_cv(frame, detections)

        image = Image.fromarray(frame[:, :, ::-1])
        draw = ImageDraw.Draw(image)
        height = frame.shape[0]
        font = self._font(max(14, height // 40))
        thickness = max(2, height // 360)

        for det in detections:
            x1, y1, x2, y2 = det.xyxy
            color = _PALETTE[det.cls % len(_PALETTE)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=thickness)

            text = f"{det.name} {det.conf:.2f}"
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
        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det.xyxy)
            color = _PALETTE[det.cls % len(_PALETTE)]
            color_bgr = (color[2], color[1], color[0])
            cv2.rectangle(frame, (x1, y1), (x2, y2), color_bgr, 2)
            text = f"{COCO_EN[det.cls] if det.cls < len(COCO_EN) else det.cls} {det.conf:.2f}"
            cv2.putText(
                frame, text, (x1 + 4, max(16, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_bgr, 2, cv2.LINE_AA,
            )
        return frame
