# Router 服务取用 seam 收口（第二轮候选 4）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：M12 composition root 已建（`composition.py`，但 router 未迁）；
> 第二轮复审 `architecture-review-20261120-150939.html` 候选 4。
> **范围**：只收 `_get_session_service` / `_session_service_cache` 的服务取用与缓存；
> `_get_permission_gateway` / `_build_llm_config` 留 chat.py（下次独立批，Q1-A）。

## 0. 目标与非目标

**目标**：服务取用与缓存单一化——composition.py 增唯一取用点，7 个 router 改调它，
删 chat.py 第二份 router 缓存后由 composition root 单点持有。

**非目标**：① 不动 `_get_permission_gateway` / `_build_llm_config`（Q1-A 下次批）；
② 不改服务构造/close 生命周期语义；③ 不立即删旧名（Q3/Q4-A 标注过渡面，下版删）；
④ 不改测试锚（本批保留 re-export 兼容）。

## 1. 现状证据

- `chat.py:102` `_session_service_cache`（第二份 router 缓存），`:194-219`
  `_get_session_service`（从 db_path 取/构造/缓存）；被 7 个 router 经
  `from chat import _get_session_service` 横向穿透（goal:282 / permission:48 /
  web_session:320 / workflow:94 / slash_common:39,46 等）。
- `composition.py` 已有 `build_resources` / `register` / `resources_from_request` /
  `close_all`——服务取用的天然单点，但 router 未走它。
- M12 建立 composition root 时明确 "router cache 自建 container 无 close" 是遗留，
  本批收口那一半。

## 2. 设计（grilling 决策落位）

| # | 决策 | 落位 |
|---|---|---|
| Q1 | 只收服务取用+缓存 | `_get_session_service` / `_session_service_cache` 迁 composition；权限网关/LLM 构造不动 |
| Q2 | 唯一取用点 | `composition.py::session_service_from(request_or_container)`——优先 request.state container 取 session_service，无则回退现有 db_path 构造路径；router 改调它 |
| Q3 | 测试锚不动 | chat.py 保留 `_get_session_service` re-export（过渡面），router 测试锚不改 |
| Q4 | 下版删旧名 | 过渡面注释标注 "DEPRECATED: 下版删"（与 M11b 同款） |

**seam 形状**：

```
composition.py:
  session_service_from(request: Request | None = None) -> SessionService
    - request 有 state._container → 取 container.session_service
    - 否则 → 按 db_path 从 _SERVICE_CACHE 取/构造（缓存单点搬到 composition）
chat.py:
  from composition import session_service_from as _get_session_service  # 过渡 re-export
  # 删 _session_service_cache 定义（缓存归 composition）
router 们:
  from composition import session_service_from  # 或仍从 chat（过渡期，下版改）
```

**缓存单点**：`_SERVICE_CACHE: dict[str, SessionService]` 从 chat.py 搬到
composition.py；close 生命周期由 composition 的 `close_all` 统一管（不再有
router 自建 container 无 close 的半吊子状态）。

## 3. 分批与验收（单提交，第一步）

| 验收项 | 落点 |
|---|---|
| 服务取用单点 | composition.session_service_from 存在且 router 改调 |
| 缓存单点 | chat.py 无 `_session_service_cache` 定义，composition 持有 |
| 过渡面保留 | chat.py re-export 名仍可用（测试锚不破） |
| 行为零变化 | 全量 pytest（含 IDOR / chat 契约 / workflow ownership 面）+ gate |
| 数字同步 | R-007 |

**回滚**：单提交回退恢复 chat.py 缓存与 router 原 import，零行为变化。

## 4. 风险与回滚

- 过渡 re-export 让 chat.py 仍暴露 `_get_session_service`——只是薄转发，
  下版删除批再清（M11b 同款）。
- `session_service_from` 的 request 回退路径必须与 chat.py 原 `_get_session_service`
  逐字等价（db_path 取法、构造参数），由测试兜底。
- close 生命周期：composition.close_all 已管 container；router 不再自建——行为
  零变化（原 router 缓存的 container 也没 close，现在统一由 composition 管）。

## 5. 明确不做

① `_get_permission_gateway` / `_build_llm_config` 迁移；② 删旧名（下版）；
③ 改服务构造/close 语义；④ 碰候选 5；⑤ 改测试锚。

## 6. 实施前确认

本方案来自 grilling 共识（#4 Round 1 全 A）。确认后按 R-007 单提交实施。

## 7. 执行记录（2026-11-20 · 已实施 · 行为零变化）

- `composition.py` 新增 `session_service_from(request=None)` 唯一取用点 +
  `_SERVICE_CACHE`（服务缓存单点，从 chat.py 归位）+ `clear_service_cache()`；
  request 路径走 `resources_from_request`，无 request fallback 逐字复用原
  chat.py 逻辑（含 composition.register 统一 close）。
- `chat.py`：删 `_session_service_cache` 独立定义，改为 composition
  `_SERVICE_CACHE` 别名（测试锚 .clear()/迭代仍生效）；`_get_session_service`
  改薄委托 `session_service_from()` + DEPRECATED 过渡标注（下版删，M11b 同款）。
- router 们本批不改 import（仍经 chat 过渡 re-export，Q3-A）；测试锚不动。
- 事实修正：`_get_session_service` 原 fallback 已经过 composition.register
  （M12 加的），本批收益是缓存/取用单点，不是生命周期修复——文档已按实记录。

验证：API 面 115 passed（chat/workflow/compact/webui 等）；全量 8038 passed
（仅数字漂移）；数字同步 464 文件 / 96,912 行（L1 +25）。
`_get_permission_gateway` / `_build_llm_config` 未动（Q1-A 下次批）。
