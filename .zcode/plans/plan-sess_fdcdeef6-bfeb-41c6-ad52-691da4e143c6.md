# foundation 结构化执行计划（依据 foundation-structure-review-20261117.md）

## Context

复评文档已确认方向：foundation 顶层 9 个实质模块做**职责归簇**——`structured_output`→`llm/`、`git`/`registry`→`utils/`、`settings`/`paths`/`config_loader`→新建 `config/`；`compact_config`/`errors`/`protocol` 留顶层（跨域契约）。用户已裁决三个归属（config_loader 归 config/、registry 归 utils/、compact_config 留顶层）。两轮只读清单已锁定全部引用形态：settings/paths 约 38 处活导入 + 5 类手工锚点；五个小模块合计 10 处活导入、零动态导入风险。全程零 re-export（R-014 纪律），绑定名保持（`from agentnodes.foundation import settings as _settings` → `from agentnodes.foundation.config import settings as _settings`）。

**并行会话约束**：另一会话正在迁移 state 簇（当前树有 event_store/memory_manager/session/store 等 13 个未提交修改，其中 app.py、routers/chat.py 同时是 settings 消费方）。**批 2 开始前必须等并行改动落地（git status 干净）**；每批提交前做碰撞检查，一律路径限定提交。

## 批次

### 批 0 · 文档先行（先做）
- 在 `docs/design/foundation-structure-review-20261117.md` 追加「§6 执行计划」：批次表、改写规则、锚点清单、验证清单（本计划内容）。
- 路径限定提交该文档。

### 批 1 · 小件归位：structured_output → llm/，git + registry → utils/
影响面 ~9 文件 / ~12 行：
- 活导入改写：`foundation/llm/parser.py:254`（lazy）、`kernel/workflow/node_types.py:20`、`kernel/agent/loop/core.py:42`、`tests/test_structured_output.py:4`、`tests/test_workspace_git_tools.py:14`、`tests/test_registry_base.py:10`、`tests/test_registry_base_extended.py:14`、`tests/test_registry_base_keys.py:4`
- `registry.py:9` docstring 用法示例路径更新；`foundation/utils/__init__.py`、`foundation/llm/__init__.py` docstring 文件清单（如有）
- `scripts/arch/layer_matrix.py`：删 3 条目（:86 registry / :90 git / :93 structured_output；新路径经 `foundation.llm`/`foundation.utils` 前缀解析为 L4）
- git mv 三文件 → 新位置；幂等改写脚本跑两遍（第二遍 0 改动）
- 验证：受影响测试（含 `PYTHONUTF8=1`，test_workspace_git_tools 有已知 GBK 编码敏感）→ 全量 → batch_gate → 路径限定提交

### 批 2 · config/ 包：settings + paths + config_loader
影响面 ~45 文件。**前置：并行会话改动已落地。**顺序：
1. **规则先行**：`.rules/rules.md` R-002(:36)/R-005(:75-76)/R-019 注(:378)/L4 行(:27) 路径文字更新为 `foundation/config/...`；历史日期设计文档（param-naming、data-dir-unification 等 5 份）不改（记录当时事实）
2. git mv 三文件 → `foundation/config/`，新建 `config/__init__.py`（纯 docstring，零 re-export）
3. 幂等改写脚本（tmp/probe/，跑两遍）：
   - 规则 B：`agentnodes.foundation.settings`→`agentnodes.foundation.config.settings`；`...foundation.paths`→`...foundation.config.paths`；`...foundation.config_loader`→`...foundation.config.config_loader`
   - 规则 A：`from agentnodes.foundation import (settings|paths|config_loader)(\s+as\s+\w+)?` → `from agentnodes.foundation.config import X<别名>`（保绑定名；A 模式不会匹配 B 输出，天然幂等）
4. 手工锚点（脚本外）：
   - `scripts/arch/check_env_access.py`：EXEMPT_FILES(:36-39) 两项改新路径；:70 lazy import 改 `from agentnodes.foundation.config import settings`；:6/:16/:145 文案
   - `scripts/arch/check_path_literals.py`：EXEMPT_FILES_C(:58-63) 改 `foundation/config/paths.py`；:53 注释；:104-105 报错文案
   - `tests/test_settings.py`：`_RAW_READ_EXEMPT`(:51-54) 两项；`(SRC/"foundation"/"settings.py")` Path 拼接(:174-177) 加 `config` 段
   - `tests/test_legacy_env_audit.py`：两处 `caplog_at("agentnodes.foundation.settings")`(:113,:164) → `agentnodes.foundation.config.settings`（logger 名 = 模块 `__name__`，随移动变化）
   - `settings.py` docstring「与 paths 分工」段(:49-54)、`paths.py` 惰性 import 注释(:106-107)—— cosmetic 同步
   - `scripts/arch/layer_matrix.py`：删 config_loader 条目(:87)，加可选条目 `"agentnodes.foundation.config": "L4"`
5. 改写前先 grep 测试内引号字符串 `"foundation/settings.py"` 类合成夹具（阶段 3 教训），命中则加排除

### 批 3 · 收尾与图同步
- `docs/architecture-diagram.html` 手工文本：:347 L4 带（git 移除）、:354「errors · protocol」（registry 移除）、:355「compact_config」（config_loader/structured_output 移除）
- `docs/architecture-layered-l3-clusters.html`：foundation/llm、foundation/utils、foundation 顶层节点数字更新 + LANES L4 行加 `foundation/config` 节点（复用 scan_arch 口径取数）
- `check_framework_numbers.py` + `sync_architecture_diagram.py --write` 每批已跑，此处终验
- graphify 重抽取 + `graph_layer_audit.py`（第二种量纲复核：0 上行违规）
- 复评文档回填三批实测数字；路径限定提交

## 关键事实（已核实）
- layer_matrix 对 settings/paths **无独立条目**（父前缀覆盖），仅 registry/config_loader/git/structured_output 四条需删
- 无 monkeypatch 字符串锚 `"agentnodes.foundation.settings.x"`；无 importlib/`__import__` 动态引用；无 `__file__` 层数敏感
- settings 主导绑定形态 `from agentnodes.foundation import settings as _settings`（30 src 文件），消费方全部经 `_settings.get_*`，别名保持后零调用点改动
- `test_default_paths.py` 是 paths 行为套件（无 test_paths.py）；其消费方相等性断言（sqlite/checkpoint_store 等）不受移动影响

## 验证（每批固定）
1. `git status` 碰撞检查（并行会话文件不在本批触碰清单）
2. 受影响测试 → 全量 `PYTHONUTF8=1 python -m pytest tests -q -p no:hypothesispytest -p no:hypothesis -n 12 --dist loadgroup`（内存不足按 rules §2.1 降档）
3. `scripts/batch_gate.cmd` 全 11 步 GREEN
4. `sync_architecture_diagram.py`（批 3 加 `--write`）+ `check_framework_numbers.py`
5. 逐文件 `git diff` 审计：变更行必须全是 import/路径行；旧路径扫描为 0
6. 路径限定提交（不 push，遵既有指示）；每批回填复评文档实测

## 不做
- compact_config / errors / protocol 不动（跨域契约留顶层）
- 不建兼容 shim、不做包级 re-export
- 不改历史日期设计文档；不动并行会话文件
- 不删任何测试；config_loader/registry 保留（已裁决）

allowedPrompts: [{"tool": "Bash", "prompt": "git mv/add/commit foundation 模块迁移与路径限定提交"}, {"tool": "Bash", "prompt": "运行 pytest 全量与受影响测试"}, {"tool": "Bash", "prompt": "运行 batch_gate.cmd 与 arch 门禁脚本"}, {"tool": "Bash", "prompt": "运行 python 改写与扫描探针脚本"}]