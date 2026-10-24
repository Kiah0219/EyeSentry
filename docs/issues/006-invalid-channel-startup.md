# 006 · 非法 `tts.channel` 启动裸 traceback 且先占用相机

- 严重度：P1
- 状态：未修复
- 范围：`main.py`
- 发现：2026-10-25 项目审计

## 现象

`config.yaml` 里把 `tts.channel` 写成 `foo`，启动时先打印一屏 Python traceback，而不是人话错误；而且相机已经被打开、TTS 线程已经启动后才抛错。

## 位置

- 先开相机 / 起 TTS：[main.py:187](../../main.py#L187) `start_runtime()` 前半段
- 才校验通道：[main.py:200](../../main.py#L200)
- 只捕获两类异常：[main.py:527](../../main.py#L527) `except (CameraError, DetectorError)`

## 根因

配置校验放在资源获取之后，且 `ValueError` 不在启动错误处理范围内。

## 建议修复

1. 在 `start_runtime()` 最前面校验 `tts.channel`（及同类枚举配置），失败直接抛带人话的 `ValueError`。
2. `main()` 捕获 `ValueError` 并 `SystemExit(f"启动失败：{exc}")`，不打印 traceback。
3. 若校验放在资源获取之后，失败路径要关闭相机、停掉 TTS worker。

## 验收

- `channel: foo` 启动：只有一行「启动失败：tts.channel 只能是 device / web / mute」，无 traceback，相机未被占用（另一个检查工具能立即打开）。
- `channel: web` / `mute` / 缺省：行为与现在一致。
