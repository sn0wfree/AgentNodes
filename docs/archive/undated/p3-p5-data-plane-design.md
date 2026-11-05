# 平台数据面收尾三件：P-3 登记 · P-4 身份 · P-5 指针（含 D-4）— 2026-10-26

> 配套：`platform-data-plane-discussion-20261026.md`（P-1/P-2 定案）。
> 状态：**P-4 ✅ P-5 ✅ D-4 ✅ P-3 ✅ 全部定案（2026-10-26）**。数据面平台侧+研究域目录侧收口。

---

## P-4 身份原语（✅ 2026-10-26 定案）

### 身份层级（四层，派生优先于签发）

```
session_id  = uuid（物理文件名，永不复用）        ← 唯一用随机 id 的层级
turn_id     = {session_id}#{n}                   ← 准入序号，SessionController 分配；7 阶段生命周期
run_id      = {turn_id}:r{m}                     ← 第 m 次执行实例；run 级生命周期 running/settled/failed/interrupted
子 run      = {run_id}:{ordinal}                 ← fanout 子代理（两级准入规则不变）
artifact_id = {run_id}:a{ordinal}                ← 该 run 产出的工件（P-3 登记行引用）
```

**turn : run = 1 : N（用户修订，不合并）**：
- 理由：一轮可能多次运行（瞬时失败重试/引擎内部续跑）——**fence 需要 run 层**：同 turn 内新 run 认领 activeRunId 即超越旧 run，旧 run 陈旧提交被 fence 拦截；合并则此语义失效。
- 先例：Temporal WorkflowId : RunId = 1:N；对应不变量——**同一 turn 同时至多一个活跃 run**（新增候选不变量 8）。
- 与「中断不自动恢复」的关系：turn 内多次 run = 运行期语义；Gateway 重启后被中断的 run 仍不自动恢复（不变）。

### session_key / session_id 分离（已确认）
- `session_key`：逻辑名，人可读（`chat:main`、`study:momentum`），可复用
- `session_id`：uuid 物理文件名，永不复用；全局登记索引维护 key→id 映射；逻辑会话"转世"拿新 uuid，历史文件不混

### 血统三件套（所有业务表统一携带，PlatformStore 自动填/校验）
| 字段 | 含义 |
|---|---|
| `created_by_run_id` | 谁产出（用户操作为 null） |
| `parent_id` | 从哪个对象派生（假设→round→工件链） |
| `created_at` | 何时 |

落表：event 信封带 run_id · message 行带 turn_id/run_id · P-3 登记行带三件套+artifact_id · goal/todo 带 created_by。

### 不变量 7/8（数据面）
7. 唯一写入者 = PlatformStore（每 session 单写连接 + 全局库单写连接）
8. 同一 turn 同时至多一个活跃 run；新 run 认领即超越，旧 run 提交被 fence 拦截

---

## P-3 工件登记（✅ 2026-10-26 收口）

全局库（platform.sqlite）登记索引表；产物文件在 workspace 研究目录（相对路径）；两步可见 `writing → registered` + registered 事件才算数。

**登记行定稿**（融合 P-4/P-5）：
`{artifact_id, kind, uri, sha256, bytes, schema_v, status, created_at, created_by_run_id, parent_id, study_id?, round_id?}`

**kind 枚举 v1**：`backtest_result`（唯一实装用户——回测结果是第一个用户）+ 预留位 `upload` / `export` / `log_bundle`（用到再加，枚举开放）。

**职能三条**：① DuckDB 单写者冲突解法（大件写文件、登记行小）② 跨会话发现唯一入口（研究列表/工件检索/goal 摘要索引同机制）③ 接缝规则执行点（研究域不私开存储通道）。

---

## P-5 指针格式（✅ 2026-10-26 定案）

四件套定形：`{uri, sha256, bytes, schema_v}`——相对路径（根=workspace）、内容指纹、大小（预算前检查）、内容格式版本（老文件可识别）。

**相对路径根 = workspace 目录**。uri 示例：`studies/study-abc/rounds/R-003/results.parquet`。

---

## D-4 round 目录布局（✅ 2026-10-26 定案，方案 A：按研究组织）

```
workspace/                        ← P-5 uri 根
├─ studies/<study_id>/
│   ├─ meta.json                  ← study 实体摘要（账本侧）
│   └─ rounds/R-00n/
│       ├─ strategy.py            ← Tier 2 研究产物（可 git 面）
│       ├─ results.parquet        ← 回测结果（P-3 登记 kind=backtest_result）
│       └─ artifacts/...          ← 图表/其他
├─ files/                         ← 非研究类杂项（kind=upload 等）
└─ archive/                       ← 归档 session 文件移放处（平台侧）
```

**选型理由（方案 A vs B 会话中心 vs C 扁平）**：
- ✅ 与「账本解耦」同构：研究资产生命周期属于 study 实体，不绑会话；整研究打包/移交/删除=移一个目录
- ✅ home_session 已保证时序归位（事件在 home session 库），产物在 study 目录，P-3 登记行（study_id/round_id/run_id）缝合两侧
- ✅ 会话归档语义干净：归档 chat 会话≠动研究资产
- ✗ 会话中心（B）违背账本解耦；扁平（C）无归属结构、全靠登记反查

**git 面**：strategy.py 等文本产物进 git（沿用现状惯例）；`*.parquet`/大文件 .gitignore 排除。
