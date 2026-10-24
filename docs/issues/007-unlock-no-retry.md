# 007 · 网页音频解锁失败后不会自动重试

- 严重度：P1
- 状态：未修复
- 范围：`static/index.html`
- 发现：2026-10-25 项目审计

## 现象

页面第一次点击或按键时静默解锁音频。如果这次解锁失败（AudioContext 构造 / `resume()` 抛错，或浏览器策略仍未放行），`unlocked` 仍是 `false`，但两个监听器已经因为 `{ once: true }` 被移除。此后告警只会提示「网页播放需要先点一下页面」，用户再点却没有反应，只能重选一次「网页」通道或刷新页面。

## 位置

- 一次性监听：[static/index.html:343](../../static/index.html#L343)
- 解锁逻辑：[static/index.html:306](../../static/index.html#L306) `doUnlock()`
- 播放拦截提示：[static/index.html:354](../../static/index.html#L354) `playWeb()`

## 根因

「只试一次」的监听策略与「解锁可能失败」的现实冲突；失败后没有重新挂载监听。

## 建议修复

去掉 `{ once: true }`，让监听器一直在，靠 `if (unlocked) return` 短路；或在 `unlockAudio()` 返回 `false` 时重新注册。顺手确认 `doUnlock()` 失败时不要置 `unlocked = true`。

## 验收

- 模拟第一次 `AudioContext.resume()` 失败：再点一次页面，解锁重试并成功；后续告警能自动播放。
- 解锁成功后再点击页面，不重复预热、不打断正在播放的音频。
