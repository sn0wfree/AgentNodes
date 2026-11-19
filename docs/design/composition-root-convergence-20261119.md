# M12 组合根收敛方案（批 4 · 候选 2）

> **性质**：R-014 方案先行 · **状态**：待维护者批准 · **日期**：2026-11-19
> **来源**：11:13 全项目架构复审候选 2（Worth exploring）+ 三前端一核心批 4/M12
> **约束**：不引入新 DI framework；不盲删 fallback；先明确三类入口再收敛。

## 0. 事实基础（Path A 盘点）

当前服务定位不是「五套独立机制」，而是**一条生产路径 + 两个 fallback + 一个独立控制面**：

| # | 机制 | 消费者 | 生命周期 |
|---|---|---|---|
| 1 | `AppContainer`（`app.state._container`，`container.py:39`） | 生产唯一构造点；`dependencies.py` 全部 provider 优先读它 | `close_container` 显式释放 5 个服务的句柄（WinError 32 教训） |
| 2 | `dependencies.py` fallback（`get_session_store` 等，`app.state` 自建） | 测试（无 container 时）；`tests/test_api_dependencies.py` 等 | `reset_app_state` 清理；**无 close**（测试进程短生命周期） |
| 3 | router `_get_session_service()`（chat/slash_common，module cache by db_path） | 无 request 上下文的调用（脚本/后台/部分测试）；41 契约 patch 锚 | **无 close**——自建 container 后只缓存 service（`chat.py:102,210-214`） |
| 4 | `app.state` bulk-assign（`services_from_container`，`container.py:200-215`） | 让旧 accessor 找到缓存（"legacy accessors stay populated"） | 与 container 同生命周期 |
| 5 | Gateway singleton | 独立控制面，不属于本候选 | 独立 |

**核心摩擦**：
- 生命周期规则分散——container 有 close，router cache 没有；`close_container` 的注释明确记录了「未关闭句柄 → WinError 32 → 全量 55 项级联失败」的历史。
- identity 规则分散——同一 SessionService/Projector 可能由 container、dependencies fallback、router cache 三条路径构造，共享身份靠约定而非结构保证。
- 但**不能盲删**：router `_get_session_service` 有 41 patch 锚，反映"无 request 上下文"真实需求（脚本/后台/测试）；`dependencies` fallback 有 `test_api_dependencies.py` 专测。

## 1. 目标形态

**一个组合根模块拥有「资源图构造 + 生命周期」规则**，三类入口各有明确 adapter：

```
interfaces/api/composition.py（新，深模块）
  interface: build_resources(db_path) -> Resources
             close_resources(resources) -> None
             resources_from_request(request) -> Resources   # 生产
  实现: AppContainer 构造 + close（从 container.py 搬入）+ 缓存策略

三类 adapter（薄）：
  - FastAPI lifespan: build → attach → yield → close（生产）
  - dependencies.py: resources_from_request(request)（保留 provider 名，内部转调）
  - router _get_session_service(): 无 request 时 build_resources(db_path) + 进程级缓存
```

**关键决策**：router 私有 accessor 保留为**薄 adapter**（决策与 3a.4 过渡面同款：签名不变、内部转调），消除"每条路径各造一套"而不破坏 41 patch 锚。close 规则集中后，router cache 里的 container 也纳入 close（测试 reset 或进程退出时）。

## 2. 分批实施（每批独立提交/回滚）

| 子批 | 内容 | 验收 |
|---|---|---|
| 1 | `interfaces/api/composition.py` 新模块：把 `build_container` / `close_container` / `services_from_container` 搬入（保持旧名薄转发到新实现，过渡） | 现有 `test_api_container.py` 全绿；零行为变化 |
| 2 | `dependencies.py` 全部 provider 内部改为 `resources_from_request(request)`，fallback 逻辑收进 composition | `test_api_dependencies.py` 全绿；identity 断言保持 |
| 3 | `chat.py::_get_session_service` 改为 composition 的 cache adapter（dcache by db_path + 纳入 close） | 41 patch 锚全绿；新增 close 行为测试 |
| 4 | `app.py` lifespan 显式 `close_resources`（替换散落的 close 调用） | 进程退出无句柄泄漏；WinError 32 回归测试 |
| 5 | 旧名（container.py 的函数）按 R-015 三查后删除或标 DEPRECATED（裁决另定） | R-016 出口网关闭；路径限定提交 |

## 3. 明确不做

- 不引入 DI framework（FastAPI Depends 已够）；不为单一消费者造抽象。
- 不碰 Gateway singleton（独立控制面，非本候选）。
- 不改 3a 已落地的 send_turn / bridge / persister 接线。
- 不在本批删任何 fallback——只在子批 5 按裁决处理。

## 4. 风险与回滚

- 41 patch 锚对 router 私有 accessor 的绑定：子批 3 保留签名 → 天然保绿；若后续迁移测试需与删除同批（参考 3a.4 裁决）。
- identity 变化风险：每批以 `test_api_container.py` + `test_api_dependencies.py` 的 identity 断言守护。
- 回滚：每子批独立提交，回退即恢复原路径。

## 5. 待确认决策点

1. 是否接受「composition.py 深模块 + 三类薄 adapter」的形状？
2. 子批 1-4 本轮实施、子批 5（删除旧名）延后下版本——是否接受（与 3a.4 裁决一致）？
3. router cache 的 close 时机：测试 `reset_app_state` 时 / 进程退出时 / 两者都做？
4. `app.py` lifespan 当前不显式调 close_container（与其它会话的产物可能冲突）——是否本批一并补齐？

（零代码改动，仅方案；批准后按 R-014 批次实施。）
