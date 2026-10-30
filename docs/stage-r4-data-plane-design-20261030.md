# R4 设计：数据面落定 —— P-3 接线 + PROTOCOL_VERSION（2026-10-30）

> 计划依据：`roadmap-next-20261030.md` R4。三道门沿用。
> **决策 D2（用户已定）：延后分库 + 仅接 P-3。** 重开条件=单库 >2GB 或出现
> 归档需求。本轮不动 per-session 分库、不动 platform.sqlite 全局库形态。

---

## 1. 勘查结论

| 原 R4 项 | 勘查事实 | 结论 |
|---|---|---|
| P-5 指针四件套 | **已完整实现**：`ArtifactPointer{artifact_id,kind,uri,sha256,bytes,schema_v}`、sha256 校验读回、D-4 目录布局 `studies/<sid>/rounds/<R>/artifacts/`（`artifact_channel.py`） | ✅ 无需新码 |
| P-3 登记 | 机制完整（`PlatformStore.register_artifact` + `ArtifactChannel.maybe_artifact`），**13 个测试**——但 **`ArtifactChannel` 零调用方**，生产从不登记 | ⚠ 待接线（本刀主体） |
| PlatformStore 接线 | `Gateway(data_dir=...)` 支持，但 `singleton.get_gateway()` **不传 data_dir** → 生产 `store is None` | ⚠ 待注入 workspace 路径 |
| PROTOCOL_VERSION | 事件信封已有 `schema_v` 字段（默认 "1"），但**无版本常量、无回放校验** | ⚠ 待补（0.5d） |

## 2. 为什么 ArtifactChannel 值得接（而不是退役）

less-is-more 的两条判据：

1. **有真实用户**：study round 的 metrics/backtest 结果会随轮次变大，
   而 `study_round` 事件已经把整个 `metrics` dict 塞进事件（`runner.py:720-724`）。
   回测 metric 集合 + 每轮 history 会持续膨胀事件行——P-3 的阈值分流正是为它设计。
2. **有现成实现和测试**：13 个测试在跑，接的是「构造 + 一处调用」，
   不是重写。

对比 PlatformStore 整体接线（含 session_registry 等 S4 未完成部分）：
**只接 artifact 登记这一条路径**，不触碰 session 登记（那是分库决策的一部分，D2 已延后）。

## 3. 设计

### 3.1 组装根接线（选项 A 同型）

```
singleton.get_gateway()
  workspace = resolve_workspace()          # SR_WORKSPACE_PATH 或默认
  gw = Gateway(..., data_dir=<workspace>/.quantnodes/platform)
```

- `data_dir` 只用于 PlatformStore；`Gateway._default_sink` 已把事件写进去。
- 失败降级：构造异常 → `store=None`（现行为），**不阻断 chat**（与
  registry/context_hub 降级同款）。
- 环境变量开关 `QNODES_PLATFORM_STORE=0` 可关闭（灰度回退位，测试用）。

### 3.2 artifact 通道注入

```
Gateway.__init__(..., artifact_channel=None)
  → 若 store 可用且未显式注入：channel = ArtifactChannel(store, workspace)
  → self.artifacts 属性暴露给执行层
```

**执行层只改一处**：`AutoresearchRunner` 的 round 结束事件
（`runner.py:720`）——metrics 先过 `maybe_artifact`：

```python
payload = self._artifacts.maybe_artifact(
    session_id=session, payload=metrics, kind="round_metrics",
    run_id=f"{sid}:R{round_num}", study_id=sid, round_id=f"R{round_num}",
) if self._artifacts else metrics
self._emit(session, "study_round", {..., "metrics": payload, ...})
```

- 小 metrics（<64KB，绝大多数）→ 事件字节**逐字节不变**（等价门）；
- 大 metrics → 事件只带指针，文件落 D-4 目录 + 登记行进全局库；
- `_artifacts` 由 scheduler 注入（`_make_emitter` 同一处组装根），
  None = 现行为（测试/无 store 路径）。

### 3.3 PROTOCOL_VERSION

```python
# core/orchestration/contracts.py 或新 core/platform/protocol.py
PROTOCOL_VERSION = "1"

def check_event_version(schema_v: str) -> None:
    """回放/订阅时校验；不匹配即拒绝（fail-fast，不猜语义）。"""
```

- `PlatformStore.replay*` 读出的每一行 `schema_v` 过校验，不匹配 →
  `ProtocolVersionError`（明确报错，不静默跳过）；
- 事件写入端默认取 `PROTOCOL_VERSION`（现硬编码 "1" → 改常量，零行为变化）；
- 不引入协商/多版本共存——单版本 + 拒绝。

### 3.4 明确不做

- per-session 分库（D2 延后）；
- platform.sqlite 的 session_registry 接线（S4，随分库）；
- `ArtifactChannel.load` 的生产读回路径（当前无消费方需要；
  已有测试覆盖，未来 UI 需要时再接）。

## 4. 测试计划（`tests/test_r4_data_plane.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | singleton 注入 data_dir | `get_gateway().store is not None`；开关置 0 时为 None |
| 2 | 小 metrics 事件等价 | 事件 data 逐字节 = 现行为（无指针） |
| 3 | 大 metrics 转指针 | 事件只含 artifact_id/uri/sha256/bytes；文件存在；登记行可查 |
| 4 | 指针读回校验 | `channel.load(pointer)` 还原 metrics；篡改文件后抛错 |
| 5 | 无 channel 路径 | `_artifacts=None` 时事件不变（等价） |
| 6 | PROTOCOL_VERSION | 常量存在；写入端默认它；replay 遇未知版本抛明确错误 |

## 5. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 小 metrics 路径逐字节不变；无 store 路径不变 |
| 测试不减 | inventory 0 removed |
| 失败集不扩大 | c1_gate 对基线 |
