# 012 · 播报模板渲染漏捕 `AttributeError`

- 严重度：P2
- 状态：未修复
- 范围：`tts.py`
- 发现：2026-10-25 项目审计

## 现象

`config.yaml` 的 `tts.template` 写成 `{n.foo}` 这类带属性访问的占位符时，`str.format()` 抛 `AttributeError`，不在 `render()` 的捕获列表里。异常一路冒到 event worker，被 `logging.exception` 吞掉：这次告警既不播报，也不入库、不广播。

## 位置

[tts.py:28](../../tts.py#L28)：只捕获 `KeyError / IndexError / ValueError`。

## 根因

捕获列表没有覆盖 `format()` 可能抛出的全部异常类型。

## 建议修复

补 `AttributeError`，或统一 `except Exception` 后回退默认文案；保留一条 warning 日志，便于发现写错的模板。

## 验收

- `template: "{n.foo}"` 触发一次：播报默认文案，告警正常入库。
- 正常模板行为不变。
