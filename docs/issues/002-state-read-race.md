# 002 · `/api/state` 并发读 DwellTracker 可能 500

- 严重度：P1
- 状态：未修复
- 范围：`web.py`、`detector.py`
- 发现：2026-10-25 项目审计

## 现象

外部脚本轮询 `/api/state` 时，偶发 500，日志里是 `RuntimeError: dictionary changed size during iteration`。

## 位置

- 读取：[web.py:136](../../web.py#L136) 在 threadpool 里调用 `runtime.detector.dwell.state()`
- 遍历：[detector.py:134](../../detector.py#L134) `state()` 直接遍历 `_first_seen.items()`
- 写入：[detector.py:100](../../detector.py#L100) 采集线程同时增删同一个 dict（由 `PyDetector._lock` 保护）

## 根因

`update()` 有锁，`state()` 没有。两个线程同时操作 `_first_seen`，遍历期间发生增删就会抛异常。

## 建议修复

给 `DwellTracker` 自己加一把锁，`update()`、`state()`、`reset()`、`seconds_for()` 都走同一把锁；或让 `state()` 通过 `PyDetector._lock` 访问。返回快照，不要把内部 dict 暴露出去。

## 验收

- 检测持续运行时，以 10 Hz 轮询 `/api/state` 5 分钟，无 500。
- 单元测试：一个线程持续 `update()`，另一个线程持续 `state()`，无异常。
