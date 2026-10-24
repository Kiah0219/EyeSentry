# 008 · TTS 合成无超时，关闭不结束播放子进程

- 严重度：P1
- 状态：未修复
- 范围：`tts.py`
- 发现：2026-10-25 项目审计

## 现象

- `synthesize()` 没有超时或取消。edge-tts 网络卡住时，唯一的 event worker 会一直挂在合成上，后续告警全部延迟；事件队列（`maxsize=16`）满了以后新事件被丢弃。
- `close()` 只往播放队列投一个 `None`，不会终止正在运行的 `afplay` / `ffplay`；服务退出后子进程可能继续响到 30 秒超时。

## 位置

- 合成：[tts.py:65](../../tts.py#L65)
- 播放循环：[tts.py:102](../../tts.py#L102)
- 关闭：[tts.py:116](../../tts.py#L116)

## 建议修复

1. 给合成加超时（例如 `asyncio.wait_for(..., timeout=10)`），超时按 F-32 降级并让 worker 继续处理下一条。
2. 把当前播放子进程记到实例字段，`close()` 时 `terminate()` / `kill()`，并 `join()` 播放线程（设短超时）。
3. 备注：`close()` 可能被调用两次（lifespan 与 `main()` 的 `finally`），保持幂等。

## 验收

- 模拟 edge-tts 挂起：10 秒内降级，后续告警仍能处理。
- 触发一次本体播放后立刻停服务：没有残留的 `afplay` / `ffplay` 子进程。
