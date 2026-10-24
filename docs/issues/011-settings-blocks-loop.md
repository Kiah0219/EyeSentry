# 011 · `/api/settings` 在事件循环里等待推理锁

- 严重度：P2
- 状态：未修复
- 范围：`main.py`、`detector.py`
- 发现：2026-10-25 项目审计

## 现象

`update_settings` 声明为 `async def`，但里面直接调用 `detector.set_enabled()`，后者要拿 `PyDetector._lock`。采集线程正在推理时，暂停 / 恢复检测的请求会卡在事件循环里等锁；这段时间内所有 HTTP 响应和 WebSocket 推送都被阻塞。CPU 推理越慢，停顿越明显。

## 位置

- 异步处理函数：[main.py:403](../../main.py#L403)
- 调用 `set_enabled`：[main.py:441](../../main.py#L441)
- 取锁并重置跟踪：[detector.py:264](../../detector.py#L264)

## 根因

把阻塞操作放在了事件循环线程里。

## 建议修复

把 `update_settings` 改成同步 `def`（FastAPI 会丢进 threadpool），或只在锁内做状态快照、把重置跟踪移到采集线程。注意别在锁内做 `model.track` 之外的重活。

## 验收

- 人为让推理持锁 300 ms，同时请求 `/api/status`：响应时间不受影响。
- 暂停 / 恢复检测仍然即时生效，跟踪 ID 与计时正确重置。
