# 阶段 3b 实施规划 — 数据面分库 + SSE 单轨 — 2026-10-26

> ⚠ **已被取代**：3b 计划由 3c/阶段 4 的实测记录取代（见 `stage-3c-scheduler-migration-design-20261027.md`）。

> 状态：**已确认（用户拍板），待实施**。
> 范围：PlatformStore（分库 + 唯一写入者）+ SSE 单轨 + 灰度 flag 切换 + P-3 登记行落库。
> 明确不做：T3 沙箱、Job Object、业务域改动、SSE 流式改进。

---

## 1. 目标（对照设计承诺）

| # | 承诺 | 现状 |
|---|---|---|
| 1 | 每 session 一个自包含 SQLite（event_log 全量永久 + blobs + messages）+ 全局小库（登记/goal 摘要/config 指纹） | ❌ 事件只在内存 sink |
| 2 | SSE 单轨（删 sse_buffer.py + bridge_v2.py） | ❌ 双轨并存 |
| 3 | PlatformStore 唯一写入者（不变量 7）+ 键控迁移 + blob 去重 | ❌ 无 |

## 2. 实施四步

### 步骤 1 · PlatformStore（2d）—— 核心中的核心

**文件**：`core/storage/platform_store.py`

**每 session 一库** `sessions/<session_id>.sqlite`：
```
event_log     (seq INTEGER PK, ts REAL, kind TEXT, data TEXT/JSON, run_id TEXT, schema_v TEXT)
blobs         (hash TEXT PK, content BLOB, created_at REAL)
messages / message_parts   ← 现有 projector 沿用，表结构不变
```

**全局小库** `data/platform.sqlite`：
```
session_registry    (session_id PK, session_key, created_at, last_active)
artifact_registry   (P-3: artifact_id, kind, uri, sha256, bytes, schema_v,
                     status, created_at, created_by_run_id, parent_id,
                     study_id?, round_id?)
schema_migrations   (domain TEXT, version INT, applied_at REAL)  ← 键控迁移
```

**关键设计决定**：
- **写入者唯一**：PlatformStore 是唯一持有 SQLite 连接的类；fence 是内置属性（非外挂）
- **emit = 事务**：写 event_log + 更新投影（messages/message_parts）+ 发通知，一个事务
- **blob 去重**：`store_blob(content) -> hash`（同哈希不重写）；`read_blob(hash) -> bytes`
- **DDL 版本**：session 库 `PRAGMA user_version`；全局库 `schema_migrations` 表
- **启动自检**：quick_check → fold 重建投影 → verify_all()（A5）

### 步骤 2 · SSE 单轨（1d）

| 动作 | 文件 |
|---|---|
| 删除 | `api/sse_buffer.py`（SSEEventBuffer） |
| 删除 | `api/session/bridge_v2.py`（桥接层） |
| 改造 | `chat.py` SSE 端点 → 直接消费 EventStore subscribe/replay |
| 改造 | sse_pusher 回调 → EventStore subscriber 列表 |
| 改造 | bridge_v2 的路由逻辑 → projector sink 直接承担 |

**验收**：`test_sse_*` 套件全绿 + `test_webui*` 不回归。

### 步骤 3 · 灰度 flag 切换（0.5d）

- `QNODES_NEW_ENTRY` 默认值从 off → **on**（旧路径保留但 deprecated）
- `session_controller._build_engine` 从 monkey-patch 升级为正常构造路径

### 步骤 4 · blob/artifact 通道（0.5d）

- `artifact_registry` 表 + `store_blob()` / `read_blob()`
- cpu 节点可写入（当前 demo 用途，阶段 4 才真正接 executor）

## 3. 验证计划

| 测试 | 验证什么 |
|---|---|
| PlatformStore 单测 | emit/record/read_blob/fence 集成/migration/quick_check |
| 分库隔离 | 两个 session 数据互不干扰；全局库跨 session 可见 |
| SSE 单轨回归 | `test_sse_*` 套件全绿（行为一致） |
| 灰度切换 | flag on/off 两条路径各自产出一致的事件序 |
| 不变量 7 | grep 全项目确认只有 PlatformStore 直连 sqlite |

## 4. 已知风险

| 风险 | 缓解 |
|---|---|
| SSE 双轨删除可能破坏 WebUI | 先跑 `test_sse_*` + `test_webui*` 建立基线，改动后逐个比对 |
| projector flush 从「边界事件」改为「每 emit」可能变慢 | 保留 flush_to_messages 开关，只改触发点不改逻辑 |
| blob 表长期增长 | P-3 登记表加 referenced_by 反向引用；无人引用的 blob 月度归档清理 |
| WebUI playwright 测试 | 基线比对后逐个修，不删测试 |

## 5. 工期

| 步骤 | 工期 | 累计 |
|---|---|---|
| 1 · PlatformStore | 2d | 2d |
| 2 · SSE 单轨 | 1d | 3d |
| 3 · 灰度切换 | 0.5d | 3.5d |
| 4 · blob/artifact | 0.5d | 4d |

比原估 3b（3d）多 1d——SSE 单轨的回归验证比预想重（WebUI 有 playwright）。

## 6. 用户确认

1. ✅ 范围和分步顺序合理
2. ✅ SSE 单轨**留到下一轮**（本轮只做步骤 1/3/4，步骤 2 后置）
