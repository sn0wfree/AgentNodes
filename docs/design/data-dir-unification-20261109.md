# 数据目录统一为 `.agentnodes`（配置文件默认位置）

> 触发：用户要求「读取配置文件的默认文件夹也需要更换，要求默认使用
> `.agentnodes`；除非启动参数中有另外设定」。
> 性质：品牌/路径一致性收尾。**不改语义、不改文件名以外的任何行为。**

---

## §0 现状：混合状态（一半已改、一半没改）

### 两个已拍板的决定（2026-11-09）

| 决定 | 选择 | 影响 |
|---|---|---|
| 会话库文件名 | **改名 + 保留 quantnodes 兼容模式** | 默认 `.agentnodes_session.db`；启动参数指定 quantnodes 时用原名 |
| 旧路径数据 | **硬切换** | 不读 `~/.quantnodes` / `~/.quantnodes-research`（除既有的一次性 copytree 迁移）|

**用户提供的关键背景**：*「未来这个项目将会被 quantnodes 使用」*。
因此不能把 brand 写死成 `.agentnodes`——必须有一个**可切换的 profile**：

- `agentnodes`（默认）→ `~/.agentnodes` + `.agentnodes_session.db`
- `quantnodes`（显式指定）→ `~/.quantnodes` + `.quantnodes_agentnodes_session.db`

切换方式：环境变量 `AN_DATA_PROFILE=quantnodes` 或 CLI `--data-profile quantnodes`。
**默认永远是 `agentnodes`**（满足用户"默认使用 .agentnodes"的要求）。

批次 9（2026-11-05）做过一次 `~/.quantnodes` → `~/.agentnodes` 的迁移，
但只覆盖了**主数据目录**。实测（`.scan_paths.py`，全仓 AST 扫描）：

### 已正确的（不需要改）

| 位置 | 状态 |
|---|---|
| `foundation/storage/sqlite.py:30` | `DEFAULT_DATA_DIR = ~/.agentnodes` ✅ |
| `sqlite.py:34` `LEGACY_DATA_DIR = ~/.quantnodes` | 仅作**迁移来源**，正确 ✅ |
| `foundation/llm/quantnodes_bridge.py:38` | `CONFIG_PATH = ~/.agentnodes/llm.json` ✅ |
| `foundation/llm/config_audit.py:115` | `~/.agentnodes` ✅ |
| `kernel/memory/persistent.py` / `fts5.py` | `~/.agentnodes/memory` ✅ |
| `kernel/permission/rules_io.py` | `~/.agentnodes/permissions.yaml` ✅ |
| `interfaces/cli/halt.py` | `~/.agentnodes/halt.json` ✅ |
| `interfaces/api/user_db.py` / `auth_tokens.py` | `~/.agentnodes` ✅ |
| `interfaces/rsi_cli.py` | `~/.agentnodes/rsi_state.json` ✅ |

### 仍在用旧品牌的（**本批要改**）

| # | 文件:行 | 当前值 | 类型 |
|---|---|---|---|
| 1 | `apps/goal/checkpoint_store.py:23` | `~/.quantnodes-research/checkpoints` | home 目录 |
| 2 | `apps/goal/workflow_config.py:30` | `~/.quantnodes-research/workflows` | home 目录 |
| 3 | `interfaces/cli/commands/slash_goal.py:67,79` | 同上的**重复定义** | home 目录 |
| 4 | `foundation/llm/model_catalog.py:84` | `_USER_CACHE_DIR_NAME = ".quantnodes"` | home 缓存 |
| 5 | `foundation/storage/session_paths.py:19` | `SESSION_DB_FILENAME = ".quantnodes_agentnodes_session.db"` | **文件名** |
| 6 | `interfaces/api/dependencies.py:46` | `Path(workspace)/".quantnodes_agentnodes_session.db"`（硬编码重复） | workspace |
| 7 | `interfaces/gateway_wiring.py:91` | `<workspace>/.quantnodes/genome` | workspace |
| 8 | `kernel/gateway/singleton.py:133` | `<workspace>/.quantnodes/platform` | workspace |

**关键发现（除品牌外的两个真问题）**：

- **#3 是重复定义**：`slash_goal.py:67/79` 与 `workflow_config.py:30` 各自
  硬编码同一个路径。改一处不改另一处会分叉——这正是 `session_paths.py`
  docstring 里记录的「历史上 sessions.db 分裂过」同类问题。
- **#5/#6 文件名硬编码两次**：`dependencies.py:46` 重新拼了一遍
  `SESSION_DB_FILENAME`，没有引用常量。

---

## §1 目标

1. **所有默认路径统一到 `.agentnodes`**（含 `.quantnodes-research` 这个
   带后缀的变体 → 并入 `.agentnodes` 子目录）。
2. **保留启动参数 / 环境变量的覆盖能力**（用户明确要求"除非启动参数中
   有另外设定"）。
3. **旧路径仍可读**（向后兼容）：`~/.quantnodes` 作为**迁移来源**保留；
   `~/.quantnodes-research` 同样作为**迁移来源**，不做硬切换。

---

## §2 设计：单一事实源 + 迁移

### 2.1 新增 `foundation/paths.py`（L4）

所有 home 级路径常量的唯一来源，**且 brand 可切换**：

```python
@dataclass(frozen=True)
class DataProfile:
    name: str                 # "agentnodes" | "quantnodes"
    home_dir_name: str        # ".agentnodes" | ".quantnodes"
    session_db_name: str      # ".agentnodes_session.db" | ".quantnodes_agentnodes_session.db"

PROFILES = {
    "agentnodes": DataProfile("agentnodes", ".agentnodes", ".agentnodes_session.db"),
    # 未来 quantnodes 集成用：保持历史路径与文件名不变
    "quantnodes": DataProfile("quantnodes", ".quantnodes",
                              ".quantnodes_agentnodes_session.db"),
}
DEFAULT_PROFILE = "agentnodes"

def active_profile() -> DataProfile:
    """AN_DATA_PROFILE env → 默认 agentnodes。未知值抛错（不静默降级）。"""

def data_dir(profile=None) -> Path:
    """AN_DATA_DIR env 覆盖 > profile.home_dir_name。"""

def data_subdir(name: str, profile=None) -> Path: ...

# 旧位置（硬切换：只用于既有的一次性 copytree 迁移，不作回退读取）
LEGACY_DIRS = (Path.home() / ".quantnodes", Path.home() / ".quantnodes-research")
```

**为什么新建而不是复用 `sqlite.py`**：`sqlite.py` 已经是 `ensure_data_dir` +
`resolve_db_path` 的家，加进去会让它同时承担「路径根」与「DB 解析」两个职责；
且 `apps/goal/*` 与 `interfaces/cli/*` 引用 `foundation.storage.sqlite` 是为了
一个常量，依赖面过大。新建 `foundation/paths.py` 只依赖 stdlib。

`sqlite.py` 的 `DEFAULT_DATA_DIR` / `LEGACY_DATA_DIR` 改为从 `paths` re-export
（保持既有导入点不破）。

### 2.2 每一处的改法

| # | 改为 | 说明 |
|---|---|---|
| 1 | `paths.data_subdir("checkpoints")` | 硬切换（不读旧 `-research`）|
| 2 | `paths.data_subdir("workflows")` | 同上 |
| 3 | **删掉重复定义**，改为 import #2 的常量 | 单一定义 |
| 4 | `_USER_CACHE_DIR_NAME` → `paths.active_profile().home_dir_name` | 缓存可再生，直接切 |
| 5 | `SESSION_DB_FILENAME` → `paths.active_profile().session_db_name` | 见 §2.3 |
| 6 | 引用 `SESSION_DB_FILENAME` 常量（不再硬编码）| — |
| 7 | `<workspace>/.agentnodes/genome`（profile 化）| 硬切换 |
| 8 | `<workspace>/.agentnodes/platform`（profile 化）| 硬切换 |

### 2.3 会话库文件名（#5）

`SESSION_DB_FILENAME` 从常量改为**函数**（因为要读 profile）：

```python
def session_db_filename(profile=None) -> str:
    return active_profile(profile).session_db_name
```

- `AN_DATA_PROFILE=agentnodes`（默认）→ `.agentnodes_session.db`
- `AN_DATA_PROFILE=quantnodes` → `.quantnodes_agentnodes_session.db`（历史名不变）

**为什么不做回退读取**（用户选了硬切换）：回退读取会让「同一个
workspace 可能存在两个会话库」这个歧义长期存在，而 `session_paths.py`
的 docstring 明确记录过这种分裂造成过事故。硬切换 + profile 显式指定
是更干净的语义：**要旧数据就显式切 profile，不靠隐式回退。**

### 2.4 启动参数覆盖（用户明确要求的部分）

现状已有的覆盖点（保留）：

| 覆盖方式 | 作用域 | 现状 |
|---|---|---|
| `AN_DATA_DIR` | 主数据目录 | ✅ 已有 |
| `AN_SESSIONS_DB` | 会话库绝对路径 | ✅ 已有 |
| `AN_WORKSPACE_PATH` | workspace 根 | ✅ 已有 |
| `AN_GOAL_DB_PATH` / `QUANTNODES_RESEARCH_GOAL_DB_PATH` | goal DB | ✅ 已有 |
| `AGENTNODES_LLM_CONFIG` | llm.json 路径 | ✅ 已有 |
| `--llm-config` CLI 参数 | llm.json 路径 | ✅ 已有 |

**本批新增**：

| 新增 | 作用 |
|---|---|
| **`AN_DATA_PROFILE`** | `agentnodes`（默认）/ `quantnodes`——**品牌切换总开关** |
| `AN_CHECKPOINTS_DIR` | #1 的目录覆盖 |
| `AN_WORKFLOWS_DIR` | #2 的目录覆盖 |
| `AN_MODEL_CATALOG_DIR` | #4 的目录覆盖 |
| `--data-profile` CLI 参数 | 等价于 `AN_DATA_PROFILE`（写入进程 env）|

优先级（统一）：**CLI 参数 > 环境变量 > profile 默认**。

`--data-profile quantnodes` 的语义：把这一进程的所有路径（home 目录、
会话库文件名、workspace 子目录）切到 quantnodes 命名——这正是
"未来被 quantnodes 使用"时需要的。

---

## §3 验证

1. **新增 `tests/test_default_paths.py`**：
   - 默认 profile 是 `agentnodes`，所有路径在 `~/.agentnodes` 之下
   - `AN_DATA_PROFILE=quantnodes` → home 目录与会话库文件名都回到旧名
   - 每个 `env` 覆盖都生效（`AN_DATA_DIR` / `AN_CHECKPOINTS_DIR` / …）
   - 未知 profile 值**抛错**（不静默降级到默认——否则用户以为切了其实没切）
2. **静态门禁**：`scripts/arch/check_path_literals.py` 加一条
   ——禁止 `src/` 下出现 `"\.quantnodes"` / `"\.agentnodes"` 字面量
   （白名单：`foundation/paths.py` 的 `PROFILES` / `LEGACY_DIRS`、
   `sqlite.py` 的迁移日志字符串）。这条能防止以后再漏。
3. **硬切换验证**：`~/.quantnodes-research` 存在时**不**被读取
   （与旧行为不同，需显式测试钉住）。
4. 全量 0 failed + batch_gate ALL GREEN。

---

## §4 明确不做

- **不迁移用户数据**（除 `ensure_data_dir` 已有的 `~/.quantnodes` → `~/.agentnodes`
  一次性 copytree；本批不扩展它）
- **不删旧目录**
- **不做旧文件名回退读取**（用户选硬切换）
- 不改 `quantnodes_bridge.py` 的模块名（它是 QuantNodes 集成的桥，名字有意义）
- 不改环境变量名 `QUANTNODES_RESEARCH_GOAL_DB_PATH`（已发布的兼容名，
  新名 `AN_GOAL_DB_PATH` 已并存）
- **不把 `agentnodes` 写死成唯一 brand**——`AN_DATA_PROFILE=quantnodes`
  必须能完整回到历史命名（为未来的 quantnodes 集成保留）

---

## §5 风险

| 风险 | 缓解 |
|---|---|
| 用户已有 `.quantnodes_agentnodes_session.db`，默认 profile 切到新名后"会话消失" | 文档写明；`--data-profile quantnodes` 一键回旧名；不删旧文件 |
| 硬切换导致 `~/.quantnodes-research` 里的 workflows/checkpoints 不再被读取 | 同上：显式 profile 切回 |
| profile 值拼错导致静默用默认 | **未知值抛错**（§3.1 有测试钉住）|
| 两处硬编码文件名（#6）再次分叉 | 改成引用常量 + 静态门禁禁止字面量 |
