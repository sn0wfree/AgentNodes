# 结构债四轮快照 — 2026-10-08（收官后验证）

> 第 4 轮：收官 wrapup 后的验证扫描，**抓到 c-4 的前端 spec 遗漏**并修复。
> 口径与前三轮一致；本轮增量在「API 删除的下游同步面」。

## 1. 四轮对比

| 指标 | 第 1 轮 | 第 2 轮 | 第 3 轮 | **第 4 轮** |
|---|---|---|---|---|
| 行数 / 文件 | 107,117 / 466 | 107,455 / 466 | 106,623 / 461 | **106,582 / 460** |
| ruff src | 71 | 3 | 0 | **0**（保持） |
| 测试 | 12,366（15 failed） | 12,287（0/0 flaky） | 12,287（0/0） | 12,287（0/0） |
| DELETE 活标签 | 17 | 17 | 5 | **0**（fixtures 注记除外） |
| **OpenAPI/前端 spec 同步** | 未检查 | 未检查 | 未检查 | **本就滞后，本轮修复** ✅ |

## 2. 本轮抓到的 c-4 遗漏（API 删除的下游面）

c-4 删除 `routers/memory.py` 与 `goal.py::market` 时**只复验了 Python 调用方**（tests/src），漏了前端 spec 快照：

| 残留 | 位置 | 处置 |
|---|---|---|
| `/api/memory/search` path | `webui/frontend/openapi.json` + `types.gen.ts` | 删（业务代码零引用，唯一消费方是生成物自身） |
| `/webui/memory/search` path | 同上 | 删（**路由本体早已不存在**——spec 幽灵条目） |
| `market` 字段（`GoalStartRequest`） | 同上 | 删（c-4 的 goal.py 字段同步） |

**未动**：`ValidateRunRequest.market`（不同 schema 的活字段）、types.gen 的对应段。

## 3. 教训（第 5 条方法论）

**API 删除的复验面 = Python 调用方 + 前端 spec 快照 + 生成物**。`openapi.json` / `types.gen.ts` 是快照型产物（`npm run gen:types` 从 openapi.json 生成），删路由/字段时必须同步三处：路由本体、`openapi.json`、`types.gen.ts`。Skeleton 的教训（标签撒谎）+ 本轮的教训（spec 滞后）同根：**记账面（标签/spec/生成物）与事实面（代码）会漂移，删除动作要跨记账面复验**。

## 4. 终态（本轮后）

```
代码债：0（ruff/C901/死代码/假选项/NameError 家族全清）
记账面：spec 与代码同步（openapi + types.gen + EXPECTED_LABELS）
测试：12,287 / 0 failed / 0 flaky
待办（非债）：branch protection 手动 · eval 基线首跑 · pyright 渐进
```
