"""TTS：文案渲染、音频合成、本体喇叭播放（PRD 7.2 / F-25~F-32）。

合成只做一次（F-26）：同一段 mp3 交给当前播报通道；通道选择在 main.py。
本体播放命令按平台选：macOS afplay，Linux 优先 ffplay，没有 ffmpeg 时用
GStreamer 的 gst-launch-1.0（Jetson 上通常带 gstreamer1.0 而不带 ffmpeg）。
"""

from __future__ import annotations

import asyncio
import logging
import os
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


def _player_env() -> dict | None:
    """nohup / SSH 起的进程常常没有 XDG_RUNTIME_DIR，此时连不上 PipeWire。"""
    if "XDG_RUNTIME_DIR" in os.environ:
        return None
    runtime = f"/run/user/{os.getuid()}"
    if not Path(runtime).is_dir():
        return None
    return {**os.environ, "XDG_RUNTIME_DIR": runtime}


def _resolve_sink(prefix: str) -> str:
    """按前缀找当前 sink 全名。

    PipeWire 在重名时会给 sink 加 `.1` / `.2` 后缀（声卡重新枚举后就会变），
    配置里只写前缀，运行时再解析成实际名字。
    """
    if not prefix:
        return ""
    try:
        out = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True, text=True, timeout=3, env=_player_env(),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return prefix
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            name = fields[1]
            if name == prefix or name.startswith(prefix + "."):
                return name
    log.warning("找不到播放 sink %r，回退系统默认输出", prefix)
    return ""


def _default_sink() -> str:
    """跟随系统默认输出，与 aibao 语音对话的 mixer-default 一致。"""
    try:
        out = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True, text=True, timeout=3, env=_player_env(),
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.startswith("auto_null") or out.startswith("Dummy"):
        log.warning("系统默认输出是虚拟设备 %r，检查 wireplumber 默认 sink", out)
    return out


def _device_command(path: Path, sink: str = "", volume: float | None = None) -> list[str] | None:
    system = platform.system()
    if system == "Darwin":
        if not shutil.which("afplay"):
            return None
        command = ["afplay"]
        if volume is not None:
            command += ["-v", f"{volume:g}"]
        return command + [str(path)]
    if shutil.which("ffplay"):
        command = ["ffplay", *_FFMPEG_ARGS]
        if volume is not None:
            command += ["-volume", str(int(round(volume * 100)))]
        return command + [str(path)]
    if shutil.which("gst-launch-1.0"):
        command = ["gst-launch-1.0", "-q", "playbin", f"uri={path.resolve().as_uri()}"]
        if volume is not None:
            command += [f"volume={volume:g}"]
        if sink:
            command += [f"audio-sink=pulsesink device={sink}"]
        return command
    return None


class TTS:
    """合成与播放。播放串行化，避免两条播报叠在一起听不清。"""

    def __init__(self, cfg: dict, audio_dir: Path):
        self.voice = str(cfg.get("voice", "zh-CN-XiaoxiaoNeural"))
        self.template = str(cfg.get("template", "检测到{n}人停留超过{dwell}秒"))
        self.engine = str(cfg.get("engine", "edge"))
        self.audio_dir = audio_dir
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        configured = str(cfg.get("sink", "")).strip()
        self.explicit_sink = "" if configured.lower() == "auto" else configured
        self._last_sink: str | None = None
        volume = cfg.get("volume")
        self.volume = None if volume is None else min(max(float(volume), 0.0), 1.0)

        self._queue: queue.Queue[Path | None] = queue.Queue(maxsize=8)
        self._worker = threading.Thread(target=self._play_loop, name="tts-player", daemon=True)
        self._worker.start()

        if _device_command(Path("x"), "", self.volume) is None:
            log.warning("找不到本体播放器（afplay / ffplay / gst-launch-1.0），本体通道将只打日志")
        else:
            log.info(
                "本体播放输出：%s，音量 %s（与系统音量相乘）",
                f"固定 {self.explicit_sink}" if self.explicit_sink else f"跟随系统默认（当前 {_default_sink() or '未知'}）",
                "系统默认" if self.volume is None else f"{self.volume:g}",
            )

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

    def _current_sink(self) -> str:
        """每次播放前解析，声卡重新枚举后名字变化也能跟上。"""
        if self.explicit_sink:
            resolved = _resolve_sink(self.explicit_sink)
            if resolved:
                return resolved
        sink = _default_sink()
        if sink != self._last_sink:
            log.info("本体播放 sink：%s", sink or "系统默认")
            self._last_sink = sink
        return sink

    def _play_loop(self) -> None:
        while True:
            path = self._queue.get()
            if path is None:
                return
            sink = self._current_sink()
            command = _device_command(path, sink, self.volume)
            if command is None:
                log.warning("无可用播放器，跳过 %s", path)
                continue
            try:
                result = subprocess.run(command, check=False, timeout=30, env=_player_env())
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("本体播放失败：%s", exc)
                continue
            if result.returncode == 0:
                continue
            fallback = _default_sink()
            if not fallback or fallback == sink:
                log.warning("本体播放返回 %s：%s", result.returncode, path)
                continue
            log.warning("本体播放返回 %s，改用系统默认 %s 重试", result.returncode, fallback)
            retry = _device_command(path, fallback, self.volume)
            if retry is None:
                continue
            try:
                subprocess.run(retry, check=False, timeout=30, env=_player_env())
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("本体播放重试失败：%s", exc)

    def close(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
