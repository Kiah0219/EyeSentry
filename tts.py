"""TTS：文案渲染、音频合成、本体喇叭播放（PRD 7.2 / F-25~F-32）。

合成只做一次（F-26）：同一段 mp3 交给当前播报通道；通道选择在 main.py。
本体播放命令按平台选：macOS afplay，Linux/Windows ffplay（PRD 7.7 路径 C）。
"""

from __future__ import annotations

import asyncio
import logging
import platform
import queue
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_FFMPEG_ARGS = ["-nodisp", "-autoexit", "-loglevel", "quiet"]

_BEEP_CANDIDATES = (
    "/System/Library/Sounds/Ping.aiff",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
)


def render(template: str, people: int, dwell: float) -> str:
    try:
        return template.format(n=people, dwell=f"{dwell:g}")
    except (KeyError, IndexError, ValueError):
        log.warning("播报模板 %r 不可用，回退默认文案", template)
        return f"检测到 {people} 人停留超过 {dwell:g} 秒"


def _device_command(path: Path) -> list[str] | None:
    system = platform.system()
    if system == "Darwin":
        return ["afplay", str(path)] if shutil.which("afplay") else None
    if shutil.which("ffplay"):
        return ["ffplay", *_FFMPEG_ARGS, str(path)]
    return None


class TTS:
    """合成与播放。播放串行化，避免两条播报叠在一起听不清。"""

    def __init__(self, cfg: dict, audio_dir: Path):
        self.voice = str(cfg.get("voice", "zh-CN-XiaoxiaoNeural"))
        self.template = str(cfg.get("template", "检测到{n}人停留超过{dwell}秒"))
        self.engine = str(cfg.get("engine", "edge"))
        self.audio_dir = audio_dir
        self.audio_dir.mkdir(parents=True, exist_ok=True)

        self._queue: queue.Queue[Path | None] = queue.Queue(maxsize=8)
        self._worker = threading.Thread(target=self._play_loop, name="tts-player", daemon=True)
        self._worker.start()

        if _device_command(Path("x")) is None:
            log.warning("找不到本体播放器（afplay / ffplay），本体通道将只打日志")

    def text_for(self, people: int, dwell: float) -> str:
        return render(self.template, people, dwell)

    def synthesize(self, text: str, name: str) -> Path | None:
        """合成 mp3。失败返回 None，由调用方走降级（F-32）。"""
        if self.engine != "edge":
            log.warning("不支持的 TTS 引擎 %r，本次不合成", self.engine)
            return None
        target = self.audio_dir / f"{name}.mp3"
        try:
            asyncio.run(self._synth(text, target))
        except Exception as exc:
            log.warning("TTS 合成失败：%s", exc)
            target.unlink(missing_ok=True)
            return None
        if not target.exists() or target.stat().st_size == 0:
            log.warning("TTS 合成未产出有效文件")
            return None
        return target

    async def _synth(self, text: str, target: Path) -> None:
        import edge_tts

        await edge_tts.Communicate(text, self.voice).save(str(target))

    def play_on_device(self, path: Path) -> None:
        try:
            self._queue.put_nowait(path)
        except queue.Full:
            log.warning("本体播报队列已满，丢弃本次播放")

    def beep(self) -> None:
        """合成失败时的本体提示音（F-32）。"""
        for candidate in _BEEP_CANDIDATES:
            path = Path(candidate)
            if path.exists():
                self.play_on_device(path)
                return
        log.warning("找不到提示音文件，本体通道静默")

    def _play_loop(self) -> None:
        while True:
            path = self._queue.get()
            if path is None:
                return
            command = _device_command(path)
            if command is None:
                log.warning("无可用播放器，跳过 %s", path)
                continue
            try:
                subprocess.run(command, check=False, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("本体播放失败：%s", exc)

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
