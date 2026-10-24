# 009 · `_last_trigger` 丢失后不清理

- 严重度：P2
- 状态：未修复
- 范围：`detector.py`
- 发现：2026-10-25 项目审计

## 现象

目标离开画面超过丢失容忍时长后，`_first_seen` 与 `_last_seen` 会被清掉，但 `_last_trigger` 保留。长跑时每个出现过的 `tid` 都会永久留下一条冷却记录；如果跟踪器复用了同一个 ID，新目标可能在旧冷却窗口内被误抑制。

## 位置

[detector.py:119](../../detector.py#L119) 清理循环只删 `_first_seen` 与 `_last_seen`。PRD 7.5 的清理步骤写的是删除 `first_seen / last_trigger`。

## 根因

实现与 PRD 7.5 不一致，且漏删一个 dict。

## 建议修复

在丢失清理时 `self._last_trigger.pop(tid, None)`，与 PRD 对齐；补一条测试：目标消失超过容忍时长后重新出现，计时从 0 开始、冷却不再残留。

## 验收

- `_last_trigger` 的大小与当前在画面内的目标数量同量级。
- 离开 3 秒后重新进入的目标立即重新计时（是否重新触发仍受冷却语义约束，按 PRD 定）。
