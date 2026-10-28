# 014 · `/api/settings` 非对象 JSON 返回 500

- 严重度：P2
- 状态：未修复
- 范围：`web.py`
- 发现：2026-10-25 项目审计

## 现象

向 `/api/settings` POST 一个数组或数字（合法 JSON，但不是对象），接口返回 500：

- 数组：`"detect" in body` 可以执行，随后 `body.get(...)` 抛 `AttributeError`。
- 数字：`"detect" in body` 直接抛 `TypeError`。

这是系统边界输入，应该返回 400 而不是堆栈。

## 位置

[web.py:192](../../web.py#L192)：`body = await request.json()` 后直接按 dict 使用。

## 建议修复

```python
if not isinstance(body, dict):
    return JSONResponse({"detail": "请求体必须是 JSON 对象"}, status_code=400)
```

更彻底的做法是给 `/api/settings` 定义一个 Pydantic 模型，由 FastAPI 统一做类型校验（也顺带解决 003 的校验顺序）。

## 验收

- POST `[]`、`1`、`null`、`"x"` → 全部 400，带中文 detail。
- 正常对象请求行为不变。
