# 隔离准入 policy 抽深模块（第二轮候选 1+2）R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：B2.5 5a 已验收（`d758f7dc`，准入 C2/C3 AST 扫描已落地）；
> 第二轮复审 `architecture-review-20261120-150939.html` 候选 1+2。
> 词汇：`GLOSSARY.md` 已新增 `IsolationAdmission` 词条。
> **范围**：把准入 policy 从 1166 行 core.py 抽成独立深模块；删测试私有缓存锚。
> 零行为变化（能力常量经 core re-export 保持消费者 import 面不变）。

## 0. 目标与非目标

**目标**：声明面（registry + 能力常量词汇）与准入 policy（门槛 / AST 扫描 / 回落）
分家——一个深模块收口全部准入知识，registry 只剩一行委托；测试经公开函数取断言面。

**非目标**：① 不改准入判定行为（C1/C2/C3 规则逐字等价）；② 不改四级能力语义；
③ 不改 loop 三处 import 源（靠 core re-export 保持）；④ 不改生产开关 / allowlist。

## 1. 现状证据

- `core.py:39-54` 能力常量 + `:50-54` forbidden effects；`:876-880` 缓存；
  `:883-970` AST 扫描；`:940-970` check_isolation_admission；`:990-1003`
  registry.timeout_capability（同文件消费）。约 110 行 policy 挤在 core.py。
- 能力常量被 loop/core.py、loop/tools.py、_isolated_exec.py 三处跨模块 import
  （探针 `admission_consumers.py`），是公共词汇而非准入私有实现。
- `test_tool_isolation_admission.py:10,20,22` 直 import 私有 `_ADMISSION_CACHE`
  并 autouse 清它——实现细节泄漏成测试契约；全局字典按 execute 强引用永不淘汰。
- 设计文档 `b25-tool-pilot-r014-20261120.md:144-146` 明说「C4/C5 不塞进 core.py」，
  5a 执行记录却把 C2/C3 常量也放进去——落位漂移。

## 2. 设计（grilling 决策逐条落位）

| # | 决策 | 落位 |
|---|---|---|
| Q1(修正) | 能力常量迁 admission，core re-export | `isolation_admission.py` 持有 `TIMEOUT_CAPABILITY_*` + forbidden effects；core.py `from ...isolation_admission import TIMEOUT_CAPABILITY_*` re-export |
| Q2 | 删缓存 | `_ADMISSION_CACHE` / MISSING 哨兵删除；扫描成纯函数；风险清单测试自担一次 AST 成本（18 工具几十毫秒） |
| Q3 | 收口声明折叠 | `effective_capability(tool)` 做三件事：读声明 → 非法值回落 → isolatable 准入回落（tool=None 也返回 unstoppable） |
| Q4 | 公开面最小 | 导出 `check_isolation_admission` + `effective_capability` + 能力常量；私有符号（ISO_INJECTED_KWARGS / _ISO_CALLBACK_ATTRS / _scan_isolation_callback_deps / forbidden effects）不导出 |
| Q5 | 两提交 | ① 纯迁移 ② 删缓存+公开面收口；每提交独立 gate |
| Q6 | 非法值回落收进 effective_capability | registry.timeout_capability 变 `return effective_capability(tool)` 一行 |
| Q7 | 命名 IsolationAdmission | GLOSSARY 已新增词条 |
| Q8 | 常量迁 admission + core re-export（修正 Q1 的循环误判） | loop 三处 import 源不变 |

**新 module interface**：

```
kernel/agent/tools/isolation_admission.py
  export: TIMEOUT_CAPABILITY_* (4), TIMEOUT_CAPABILITIES,
          check_isolation_admission(tool) -> str | None,
          effective_capability(tool) -> str
  private: ISO_INJECTED_KWARGS, _ISO_CALLBACK_ATTRS,
           _ISOLATION_FORBIDDEN_EFFECTS, _scan_isolation_callback_deps

core.py:
  from .isolation_admission import (TIMEOUT_CAPABILITY_*, ...,
      check_isolation_admission, effective_capability)  # re-export，消费者零改动
  ToolRegistry.timeout_capability(name):
      return effective_capability(self._tools.get(name))
```

**循环规避**：admission 模块自给自足（常量+policy 都在它内部），不 import core；
core 单向 import admission 并 re-export。loop 三处仍从 core import 常量（import 面零变化）。

## 3. 分批与验收

| 提交 | 内容 | 验收 |
|---|---|---|
| ① 纯迁移 | 新建 admission module（常量+policy 逐字迁入）；core.py 删 policy 实现、改 re-export + registry 一行委托；测试 import 源改公开函数（缓存暂时保留在 module 里） | 准入面 12 passed；loop 三处 import 不改能编译；全量 + gate；行为逐字不变 |
| ② 删缓存+收口 | 删 `_ADMISSION_CACHE` / MISSING / autouse fixture；扫描成纯函数；测试经公开函数断言 | 准入 12 passed（monkeypatch 用例真触达扫描）；全量 + gate；数字同步 |

**回滚**：两提交独立可回滚；回退②恢复缓存实现；回退①恢复 core.py 内联 policy。

## 4. 风险与回滚

- 常量迁移后 core re-export 必须覆盖 loop 三处消费者 import 的所有名字
  （TIMEOUT_CAPABILITY_UNSTOPPABLE / ISOLATABLE / TIMEOUT_CAPABILITIES）——
  编译 + 全量测试兜底。
- 删缓存后 registry 每次查询对 isolatable 声明工具做 getsource+AST——当前零生产
  消费者，成本可忽略；风险清单测试一次扫 18 工具几十毫秒。若未来生产启用
  isolatable 且性能成问题，另立批优化（YAGNI）。
- `effective_capability(tool=None)` 返回 unstoppable——与 registry 原"未注册回落"
  行为逐字等价。
- 测试不再 import 私有量——Q2 的 monkeypatch 用例改为直接 patch `inspect.getsource`
  （实时生效，不经缓存）。

## 5. 明确不做

① 改准入规则（C1/C2/C3 逐字等价）；② 改四级能力语义；③ 改 loop import 源；
④ 改开关/allowlist；⑤ 碰其他 core.py 内容（surgical）。

## 6. 实施前确认

本方案来自 grilling 共识（Round 1 全 A + Q8 选 B 修正 Q1，Q6/Q7 采纳推荐默认）。
确认后按 R-007 两提交实施。

## 7. 执行记录

### 提交① · 纯迁移（2026-11-20 · 已实施 · 行为逐字不变）

- 新建 `kernel/agent/tools/isolation_admission.py`：四级能力常量、forbidden
  effects、ISO_INJECTED_KWARGS / _ISO_CALLBACK_ATTRS、C2/C3 AST 扫描、
  check_isolation_admission、effective_capability 全部逐字迁入；公开面
  仅 5 常量 + 2 函数（私有扫描器/常量集不导出）。
- `core.py`：删 109 行 policy 实现，改 re-export import（loop 三处消费者
  import 源零变化）；`ToolRegistry.timeout_capability` 改一行
  `return effective_capability(self._tools.get(name))`。
- 测试 import 源改 `isolation_admission`（缓存暂留，commit② 删）。

验证：准入+decision 36 passed；全量 8033 passed（仅数字漂移）；数字
同步 464 文件 / 96,862 行（L3 +1 文件 +48 行）。

### 提交② · 删缓存+扫描纯函数化（2026-11-20 · 已实施）

- 删 `_ADMISSION_CACHE` / `_ADMISSION_MISSING` / 缓存读写逻辑——扫描成
  纯函数（无全局可变状态、无 execute 函数强引用）。
- 测试删 autouse 清缓存 fixture + 私有 `_ADMISSION_CACHE` import；monkeypatch
  `inspect.getsource` 用例实时生效（不再依赖清缓存）。
- 新增 `TestEffectiveCapability` 4 例直测公开函数（未注册 / 非法值 /
  isolatable 准入失败 / 合法 isolatable 保留）。

验证：准入面 16 passed；数字同步 464 文件 / 96,853 行（L3 -9，删缓存）。
全量 + gate 见提交前记录。

**候选 1+2 全部收口**：准入 policy 成独立深模块（core.py 减 109 行、registry
一行委托）、扫描纯函数化（测试不碰私有量、无全局强引用）。零行为变化、
loop 三处 import 面不变。
