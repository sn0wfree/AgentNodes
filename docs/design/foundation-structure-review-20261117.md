# foundation 顶层模块结构化复评与拆分建议

> **日期**：2026-11-17  
> **状态**：只读复评 / 方案建议；未批准迁移，未修改实现。  
> **范围**：`src/agentnodes/foundation/` 顶层 Python 模块与现有 `llm/`、`utils/` 子包。

## 0. 结论摘要

foundation 值得做**克制的职责归簇**，但不应照搬 `kernel/agent` 的大规模下沉模式。当前 foundation 有 9 个实质顶层模块和 5 个已有子包；顶层模块彼此耦合很少，主要整理收益是让配置入口、LLM 解析器和通用工具各归其域，而不是单纯减少根目录文件数。

建议采用分层决策，而非一次性清空顶层：

1. **明确成簇**：`settings.py` 与 `paths.py` 共同构成配置读取和数据路径解析边界，可进入新建的 `foundation/config/`。
2. **语义归位**：`structured_output.py` 可进入既有 `foundation/llm/`；`git.py` 可进入既有 `foundation/utils/`。
3. **先裁决生命周期，再决定搬迁**：`config_loader.py`、`registry.py` 当前未发现生产代码静态导入，不应仅为了收空顶层就移动或删除。
4. **共享契约先留顶层**：`errors.py`、`protocol.py` 保持在 `foundation/` 根部。`compact_config.py` 也暂留根部：它同时被 LLM 配置与 kernel agent 压缩机制消费，不宜仅凭 LLM 配置的一侧使用就归进 `llm/`。

因此，这不是“foundation 不接受拆分”，也不是“9 个文件全部归入子包”。推荐方向是**先归位强语义模块，保留跨域契约，对未使用抽象另立去留判断**。

## 1. 现状与判断口径

### 1.1 目录现状

foundation 顶层实质模块：

```text
settings.py             config_loader.py        compact_config.py
paths.py                structured_output.py     git.py
registry.py              errors.py                protocol.py
```

现有子包：`llm/`、`storage/`、`observability/`、`utils/`、`web/`。`foundation/__init__.py` 只有包说明，不承担聚合导入。

本评估以实际职责、静态导入方向和既有治理约束为准。静态搜索没有发现某模块的生产导入，不等价于证明它可删除；删除前还需查动态导入、插件入口、对外 API 契约及测试意图。

### 1.2 复评中纠正的判断

上一轮有两处结论需要收窄：

- `errors.py` **不是生产零引用**：`foundation/llm/errors.py:10-13` 从 foundation 根部导入并继承 `ConfigError`。它是异常层级的共享根，必须按跨域契约处理。
- `config_loader.py`、`registry.py` 的静态生产消费者缺失，只能说明它们当前没有明显生产调用方，不能据此直接称为死代码；两者都有实现和测试，需要另作“保留、推广或退役”的生命周期决定。

## 2. 逐模块复评

| 模块 | 当前职责与证据 | 静态生产引用概况 | 建议 |
|---|---|---|---|
| `settings.py` | 进程环境配置唯一声明表，含 `ENV_TABLE`、类型读取和旧名诊断；模块说明见 `settings.py:1-67`，声明表从 `:149` 起 | 广泛被多层使用；环境变量治理明确指向该入口 | **迁入 `config/`**。这是配置域核心，但迁移必须同步治理路径和日志/测试引用 |
| `paths.py` | profile、数据根目录、子目录与 session DB 文件名；`paths.py:24-25` 承诺路径计算零外部依赖，`:105-108`、`:137-143` 惰性依赖 settings | 被 foundation/storage、LLM bridge、接口层等使用；与 settings 的分工写在 `settings.py:49-54` | **与 settings 同迁入 `config/`**，避免配置读取和路径解析分离 |
| `config_loader.py` | CLI/workspace/user/default 四层 YAML 配置加载及 Builder，见 `config_loader.py:1-24`、`:41-109`、`:154-231` | 当前源码静态扫描未发现生产导入；有 `tests/test_config_loader.py` 覆盖 | **有条件归入 `config/`**：若决定保留该通用 API，就与配置域一起移动；若产品不再采用它，先单独评估退役，不能把“移动”当成已被生产使用 |
| `structured_output.py` | LLM tool-call 参数的多级降级解析器，见文件首部和 `:37-94` | `foundation/llm/parser.py` 延迟导入 `get_parser`（`:254`）；`kernel/workflow/node_types.py` 直接使用 | **建议迁入 `llm/`**。它的主职责是 LLM 输出解析；kernel 对 L4 的消费符合依赖方向，不构成上行依赖 |
| `compact_config.py` | 纯 dataclass 配置形状；文件说明明确 LLM 配置需要它，同时禁止 foundation 引入 kernel（`compact_config.py:1-6`、`:85-91`） | `foundation/llm/config.py:49` 与 `kernel/agent/compact/core.py:213` 都直接依赖 | **暂留顶层**。它是 L4 提供给不同子域的共享配置契约；移入 `llm/` 虽技术上仍属 L4，但会把 agent 压缩配置误标成 LLM 私有实现。若后续确认唯一所有者再调整 |
| `git.py` | workspace Git 操作封装，含提交、重置和 hash 查询，见 `git.py:7-63` | 当前生产消费者是 `kernel/agent/loop/core.py` 的 `git_commit` | **可迁入 `utils/`**。属于通用基础设施候选；移动不应改变其副作用或 Git 命令语义 |
| `registry.py` | 泛型 `Registry[T]`，见 `registry.py:1-13`、`:22-90` | 当前源码静态扫描未发现生产消费者；有多个 registry 单测 | **暂不默认迁移**。若决定保留并在生产中推广，`utils/` 是可选归属；若未采用，先裁决是否退役，避免把未使用抽象放进泛化工具箱 |
| `errors.py` | `StrategyResearchError` 与共享异常根/中层类型，见 `errors.py:1-48`、`:56-169` | `foundation/llm/errors.py:10-13` 明确依赖 `ConfigError` | **留在 foundation 顶层**，作为跨子包异常契约 |
| `protocol.py` | 事件 envelope schema 版本契约，见 `protocol.py:1-10`、`:15-32` | 被 storage、kernel/events、agent.event_store、orchestration 使用；文件说明给出 L4/L3 共用原因 | **留在 foundation 顶层**，作为跨层协议契约 |

### 2.1 目标结构（建议态）

```text
foundation/
├── config/
│   ├── settings.py
│   ├── paths.py
│   └── config_loader.py       # 可选：生命周期裁决为保留且归入配置域时
├── llm/
│   └── structured_output.py
├── utils/
│   └── git.py
├── compact_config.py         # 暂留：LLM + kernel agent 共用配置形状
├── errors.py                 # 跨子包异常根
├── protocol.py               # 跨层事件协议契约
├── registry.py               # 暂留，等待保留/推广/退役裁决
└── __init__.py               # 包说明；不做 re-export
```

`config_loader.py` 若生命周期裁决为保留且归入配置域，则放在 `config/`；若尚未裁决或决定暂不归簇，则继续留在当前顶层。正式执行计划应在开始迁移前选定，不要同时保留两个副本或建立兼容 shim。

目标子包继续遵循零 re-export：调用方导入具体模块路径，不从 `config`、`llm` 或 `utils` 包入口转发符号，以避免包门面造成 monkeypatch 目标分裂。

## 3. 治理约束和迁移成本

### 3.1 配置路径是可迁移约定，不是禁止迁移的架构红线

`.rules/rules.md` 中有明确路径文字：

- R-002（`:33-40`）要求环境变量经 `foundation/settings.py` 的 `ENV_TABLE` 声明并由 settings 读取；
- R-005（`:72-77`）要求从 `foundation/paths.py::PROFILES` 取得数据目录名。

这些规则需要随迁移更新，但不否定 `foundation/config/`。迁移前应先定新的权威路径，例如 `foundation/config/settings.py` 和 `foundation/config/paths.py`，再更新规则文本、门禁和文档，不能让旧路径继续成为隐式权威。

### 3.2 必须同步的门禁与测试

已核实的路径钉位包括：

- `scripts/arch/check_env_access.py`：settings/paths 相关白名单和路径判定；
- `scripts/arch/check_path_literals.py`：paths 模块路径/数据目录约束；
- `scripts/arch/layer_matrix.py:80-93`：foundation 顶层模块显式层级登记；移动后应改为新模块前缀，确保新子模块仍解析为 L4；
- 测试中的模块路径和 logger 名称，例如 `tests/test_legacy_env_audit.py` 中 `agentnodes.foundation.settings` 的 caplog 锚点，以及各 settings/paths 测试的直接导入；
- 活跃设计文档、规则、README/运维说明中的旧路径字面量。

迁移前要按四种形式扫描：import 语句、字符串路径、Path 拼接、f-string 拼接。已检查的这批模块没有 `__file__` 路径层数敏感用法；这一点不替代完整的引用形式扫描。

### 3.3 层级与依赖方向

所有目标都留在 `foundation`（L4），所以移动本身不改变四层编号。仍需运行 `layer_matrix` 和 `check_dangling_imports.py --layers`：新子包必须由最长前缀解析为 L4，且不能因为包入口 re-export 引入新的符号级/导入级环。

## 4. 执行建议（待确认后才实施）

### 阶段 0：先裁决模块生命周期

对 `config_loader.py`、`registry.py` 分别作保留、推广或退役决定。特别是 `config_loader.py` 的 docstring 描述了多个历史配置合并场景，但当前 LLM 配置实现并未因此自动成为其消费者；应确认这是仍有意维护的通用 API，还是未完成/废弃抽象。裁决前保留代码，不删、不伪称已被生产复用。

### 阶段 1：低风险职责归位

可先处理 `structured_output.py → llm/` 和 `git.py → utils/`，验证零行为变化、具体模块导入保持唯一真身、测试及门禁通过。若执行顺序需要更保守，可分开提交，不将两个无关模块硬合成一次逻辑改动。

### 阶段 2：config 子包

将 `settings.py` 与 `paths.py` 同批迁入 `config/`；只有阶段 0 确认保留 `config_loader.py` 后，才决定它是否随批迁入。先更新 R-002/R-005、门禁和层级登记，再改生产代码/测试/文档引用。settings 的大引用面应通过自动化、幂等改写处理，并逐文件审阅改写差异。

### 阶段 3：可选归并

仅在生命周期和语义所有者明确后，再评估 `compact_config.py` 是否归 `llm/`、`registry.py` 是否归 `utils/`。默认不做跨域误归类，也不以“顶层文件数归零”为目标。

每个执行批次仍需遵循项目既定流程：操作前检查 Git 状态和既有改动；先定文档/规则，再改实现；替换脚本跑两遍验证幂等；检查全部路径引用形式；运行受影响测试、全量 pytest、`batch_gate.cmd`、架构层门禁和相关图同步；最后做路径限定提交，不夹带并行会话改动。

## 5. 本轮未做事项

- 未修改任何 Python 源码、测试、门禁或规则文件；
- 未移动、删除或新增兼容导出；
- 未决定 `config_loader.py`、`registry.py` 的最终去留；
- 未开始拆分实施，也未提交代码。

## 6. 执行计划（2026-11-18 定稿，经用户裁决）

### 6.1 归属裁决

| 模块 | 裁决 |
|---|---|
| `config_loader.py` | **保留并归入 `config/`**（随批 2 迁移；保留测试，作为配置域通用件） |
| `registry.py` | **保留并归入 `utils/`**（保留 3 个测试文件） |
| `compact_config.py` | **暂留顶层**（llm/config 与 kernel agent 双消费方，归 llm/ 会误标所有权） |

### 6.2 批次

| 批 | 内容 | 影响面 | 前置 |
|---|---|---|---|
| **1** | `structured_output → llm/`，`git + registry → utils/` | ~9 文件 / ~12 行 | 无 |
| **2** | `settings + paths + config_loader → config/`（新建包） | ~45 文件 | **并行会话 state 簇改动落地（树干净）**；`.rules/rules.md` 先行更新 |
| **3** | 架构图手工文本同步（两份 HTML）+ graphify 复核 + 本文档回填实测 | 文档 | 批 1、2 完成 |

### 6.3 批 1 明细

- 活导入 8 处：`foundation/llm/parser.py:254`（lazy）、`kernel/workflow/node_types.py:20`、`kernel/agent/loop/core.py:42`、`tests/test_structured_output.py:4`、`tests/test_workspace_git_tools.py:14`、`tests/test_registry_base*.py`×3
- `registry.py:9` docstring 示例路径；`scripts/arch/layer_matrix.py` 删 3 条目（:86/:90/:93；新路径经 `foundation.llm`/`foundation.utils` 父前缀解析 L4）
- 零 monkeypatch 字符串锚、零动态导入、零 `__file__` 敏感（已核实）

### 6.4 批 2 明细

1. `.rules/rules.md` 先行：R-002(:36)、R-005(:75-76)、R-019 注(:378)、L4 行(:27) 路径文字 → `foundation/config/...`；历史日期设计文档不改
2. git mv 三文件 → `foundation/config/`；新建 `config/__init__.py`（纯 docstring，零 re-export）
3. 幂等改写（跑两遍，第二遍 0 改动）：
   - 规则 B：`agentnodes.foundation.{settings,paths,config_loader}` → `agentnodes.foundation.config.X`
   - 规则 A：`from agentnodes.foundation import (settings|paths|config_loader)(\s+as\s+\w+)?` → `from agentnodes.foundation.config import X<别名保>`
   - A 模式不匹配 B 输出（`foundation.config` ≠ `foundation `），天然幂等
4. 手工锚点：
   - `scripts/arch/check_env_access.py`：EXEMPT_FILES(:36-39) 两项、:70 lazy import、:6/:16/:145 文案
   - `scripts/arch/check_path_literals.py`：EXEMPT_FILES_C(:58-63)、:53 注释、:104-105 文案
   - `tests/test_settings.py`：`_RAW_READ_EXEMPT`(:51-54)、`(SRC/"foundation"/"settings.py")`(:174-177) 加 `config` 段
   - `tests/test_legacy_env_audit.py`：两处 `caplog_at("agentnodes.foundation.settings")`(:113,:164) → `agentnodes.foundation.config.settings`（logger 名随模块移动变化）
   - `settings.py` docstring 分工段(:49-54)、`paths.py` 惰性注释(:106-107) cosmetic
   - `layer_matrix.py` 删 config_loader 条目(:87)、加 `"agentnodes.foundation.config": "L4"`
5. 改写前 grep 测试内引号路径夹具（阶段 3 教训）

### 6.5 每批固定验证

1. `git status` 碰撞检查（不触碰并行会话文件）
2. 受影响测试 → 全量 `PYTHONUTF8=1 … -n 12 --dist loadgroup`（不足按 rules §2.1 降档）
3. `batch_gate.cmd` 11 步 GREEN；`sync_architecture_diagram.py` + `check_framework_numbers.py`
4. 逐文件 diff 审计（变更行全为 import/路径行）；旧路径扫描为 0
5. 路径限定提交（不 push）；回填本文档实测

### 6.6 已核实的关键事实

- layer_matrix 对 settings/paths 无独立条目（父前缀覆盖），仅 4 条需删
- 无 monkeypatch 字符串锚、无 importlib/`__import__` 动态引用、无 `__file__` 层数敏感
- settings 主导绑定 `from agentnodes.foundation import settings as _settings`（30 src 文件），别名保持后零调用点改动
- `test_default_paths.py` 为 paths 行为套件；其消费方相等性断言不受移动影响

### 6.7 执行实测回填（2026-11-18 完成）

| 批 | 提交 | 实测 |
|---|---|---|
| 0 | `18b05fe0` | 复评文档 + §6 计划定稿 |
| 1 | `6a5689cd` | 3 文件 git mv；改写 11 文件 13 处（脚本自身拼接 bug 抓到并修复）；受影响 167 passed；全量 7606 / 0 failed；batch_gate GREEN |
| 2 | `71bec0f8` | 3 文件 git mv + 新建 `config/__init__.py`；幂等改写 57 文件 69 处（B=14 A=55，第二遍 0 改动）；手工锚点 20 处（env_access/path_literals 白名单、test_settings 豁免+Path 拼接、layer_matrix、caplog 锚 ×2 随规则 B 自动愈合、cosmetic 注释 7 处）；受影响 197 passed；全量（-n 4）7623 passed / **2 failed 归因 f01555d5**（mailbox.py 新增 threading 违反边界契约测试 + Gateway cancel_session 语义，非本批引入，留待 Gateway 线） |
| 3 | 本提交 | l3-clusters HTML 全表同步（9 节点数字 + 新增 foundation/config 节点 n=41/files=4/lines=1376 + LANES L4 拆两行 4+3 防溢出 + EDGES 7 条更新：L1>C1 56→46（api→agent 16→7，路由切换证据）、C1>L4 50→35（agent→config 5 新边）、C2>L4 19→30）；graphify5 第二量纲复核（矩阵全下行、core 残留 0）；全表复测节点漂移 0 |

**foundation 终态**：顶层 4 文件（errors / compact_config / protocol / `__init__`——纯共享契约）+ 6 子包（config·llm·storage·utils·observability·web）。全仓 443 文件 / 92,892 行，上行违规 0。
