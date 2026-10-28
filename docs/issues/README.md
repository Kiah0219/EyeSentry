# Issue 台账

2026-10-25 项目审计发现的代码 bug 与风险。一 issue 一个文件，修复后把状态改为「已修复」并注明提交。

严重度：

- **P0**：功能错误，会产生错误播报或告警，修前不建议做真机验收。
- **P1**：可靠性 / 数据 / 体验问题，长时间运行或异常路径会暴露。
- **P2**：小问题或边界情况，不阻塞验收。

| 编号 | 严重度 | 标题 | 范围 | 状态 |
|---|---|---|---|---|
| [001](./001-non-person-dwell.md) | P0 | 非人目标也会触发停留事件与播报 | detector.py / PRD 7.5 | 未修复 |
| [002](./002-state-read-race.md) | P1 | `/api/state` 并发读 DwellTracker 可能 500 | web.py / detector.py | 未修复 |
| [003](./003-settings-partial-apply.md) | P1 | `/api/settings` 校验顺序导致部分生效 | web.py | 未修复 |
| [004](./004-settings-ui-swallows-400.md) | P1 | 前端设置同步忽略非 2xx 响应 | static/index.html | 未修复 |
| [005](./005-alert-storage-growth.md) | P1 | 告警与音频只增不减，状态轮询全量读文件 | alerts.py / tts.py / web.py | 未修复 |
| [006](./006-invalid-channel-startup.md) | P1 | 非法 `tts.channel` 启动裸 traceback 且先占用相机 | runtime.py / main.py | 未修复 |
| [007](./007-unlock-no-retry.md) | P1 | 网页音频解锁失败后不会自动重试 | static/index.html | 未修复 |
| [008](./008-tts-timeout-and-orphan-player.md) | P1 | TTS 合成无超时，关闭不结束播放子进程 | tts.py | 未修复 |
| [009](./009-stale-last-trigger.md) | P2 | `_last_trigger` 丢失后不清理 | detector.py | 未修复 |
| [010](./010-channel-read-thrice.md) | P2 | 一次事件内三次读取 `self.channel` | runtime.py | 未修复 |
| [011](./011-settings-blocks-loop.md) | P2 | `/api/settings` 在事件循环里等待推理锁 | web.py / detector.py | 未修复 |
| [012](./012-render-attribute-error.md) | P2 | 播报模板渲染漏捕 `AttributeError` | tts.py | 未修复 |
| [013](./013-frontend-status-details.md) | P2 | 告警计数、初始通道闪烁、截图 404 无兜底 | static/index.html | 未修复 |
| [014](./014-settings-body-validation.md) | P2 | `/api/settings` 非对象 JSON 返回 500 | web.py | 未修复 |

建议顺序：001 → 006 → 003 / 004 → 002 / 005 → 007 / 008 → 其余。
