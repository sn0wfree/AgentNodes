# 批 D · LLM 配置双路径兼容 + `config.yaml` 新格式

> **本文档先行**。用户拍板（2026-11-11）：
> 1. **双路径 + config.yaml 都做**；
> 2. 读取优先级 **config.yaml 优先，llm.json 回退**；
> 3. `AGENTNODES_LLM_CONFIG` **保持单文件覆盖**（不做多路径）。
>
> **关键约束（用户原话）**：「这两个都兼容吧，因为 quantnodes 使用
> llm.json 进行配置」——**llm.json 必须永久继续工作**，不是"读一次提示升级"。

---

## §0 结论与两个前置问题

### 0.1 先修的真 bug：`~/.quantnodes/llm.json` 现在完全读不到

实测（2026-11-11）：

```python
QB.CONFIG_PATH  = C:\Users\1\.agentnodes\llm.json
QB.CONFIG_PATHS = (C:\Users\1\.agentnodes\llm.json,)     # ← 单元素！
```

`CONFIG_PATHS` 只有一项。历史原因：2026-11-05 的
`bd3f6009 fix(rename): ... 统一数据目录到 ~/.agentnodes` 把 `CONFIG_PATH`
从 `~/.quantnodes/llm.json` 改成了 `~/.agentnodes/llm.json`，**但
`CONFIG_PATHS` 仍是 `(CONFIG_PATH,)`——旧路径就此退出读取链**。

同时十几处 docstring 仍写着「`~/.quantnodes/llm.json` 是兼容回退」
（`config.py:314`、`quantnodes_bridge.py:145`、`conftest.py:411`），
**文档与代码不一致，且方向是"文档承诺了不存在的兼容"**——
这正是用户说的"quantnodes 用 llm.json 配置"会踩的坑。

本机实证：

| 文件 | 大小 | mtime | 谁在用 |
|---|---|---|---|
| `~/.quantnodes/llm.json` | 396 B | 2026-10-26 14:31 | quantnodes（**当前读不到**）|
| `~/.agentnodes/llm.json` | 494 B | 2026-11-09 16:36 | agentnodes（当前唯一被读）|

两者 session 同构（都是 `{"llm": {active_profile, timeout, max_retries,
profiles: {...}}}`），但**值不同**（model 分别是 `minimax-M3` / `MiniMax-M3.1-Flash`，
新文件还多 `enable_thinking`）——所以"读哪个"是**实质行为差异**。

### 0.2 config.yaml 的定位

`config.yaml` 是 **agentnodes 侧的新格式**，不是给 quantnodes 用的。
quantnodes 只写 `llm.json`，永远走回退路径 → **行为完全不变**。

一句话：**llm.json 是 quantnodes 契约，config.yaml 是 agentnodes 的新家，
两者平级共存，不存在"淘汰"关系。**

---

## §1 读取链（最终形态）

```
LLMConfig.load(env=...)
  │
  ├─ 1) 显式 env 覆盖（AGENTNODES_LLM_CONFIG）
  │      └─ 命中即**只用它**（单文件覆盖，不建链）
  │
  ├─ 2) 路径链（按序，第一个存在的赢）
  │      a. ~/.agentnodes/config.yaml      ← 新格式，优先
  │      b. ~/.agentnodes/llm.json         ← agentnodes 老位置
  │      c. ~/.quantnodes/llm.json         ← **quantnodes 契约（本批修复）**
  │
  ├─ 3) AGENTNODES__LLM__* env
  └─ 4) 代码默认值
```

**为什么 config.yaml 排在 `.agentnodes/llm.json` 之前**：同一目录下用户
若两个都写了，说明他在迁移中——新格式更可能是"他想要的最新意图"。
而 `.quantnodes/llm.json` 排最后，因为它属于**另一个消费方**（quantnodes），
本进程只做兼容读取，不该压过 agentnodes 自己的配置。

**"第一个存在的赢"不是"第一个文件的字段覆盖第二个"**：不做深合并。
理由：跨文件合并 LLM 配置会让"这个值从哪来"变得不可诊断；而
profiles 结构本身已提供了"一份文件内的多档位"。跨文件合并留待
明确的用户需求。

---

## §2 config.yaml 格式

沿用 llm.json 的 `llm` 段结构（**同一个 dict，换个载体**），
不设计第二套 schema：

```yaml
version: 2          # 仅 config.yaml 有；用于将来演进
llm:
  active_profile: minimax
  timeout: 300
  max_retries: 2
  profiles:
    minimax:
      provider: minimax
      model: MiniMax-M3.1-Flash
      base_url: https://api.minimaxi.com/v1
      api_key: env:MINIMAX_API_KEY      # 同 llm.json 的 env: 引用语法
      model_context_tokens: 200000
      model_max_output_tokens: 32768
      enable_thinking: true
```

**逐字沿用 llm.json 的 `llm` 段**——包括：
- `profiles` / `active_profile` 解析；
- `api_key: env:VAR` 展开（**密钥永不内联**）；
- `compact` 段；
- `AGENTNODES__LLM__*` env 覆盖；
- 全部类型强转（`timeout`→`timeout_s` 等）。

**`version` 键**：只在 config.yaml 里读，缺省视为 1；未知高版本 warn
但不拒（向前兼容）。llm.json 里若也出现 `version` 同样忽略不报错。

---

## §3 实现

### 3.1 读取（`foundation/llm/quantnodes_bridge.py` + `config.py`）

| 改动 | 说明 |
|---|---|
| `CONFIG_PATHS` 恢复三元素 | `(~/.agentnodes/config.yaml, ~/.agentnodes/llm.json, ~/.quantnodes/llm.json)` |
| `CONFIG_PATH` 保持 `~/.agentnodes/llm.json` | 语义变成"默认写入目标"，不再等于"唯一读取位置"（docstring 同步） |
| 新增 `_load_any_path(path)` | 按后缀分派：`.yaml`/`.yml` → YAML 解析；其余 → JSON。返回 `llm` 段或 `None`（文件不存在）|
| `load_quantnodes_llm_config` 走链 | 逐路径调 `_load_any_path`；第一个返回非 `None` 的赢 |
| YAML 解析 | `import yaml` 已在 CLI 依赖里（`__init__.py` 顶层 import yaml）；缺失时降级为"跳过该路径 + warn"，不崩 |

**错误语义**（沿用现有）：
- 文件不存在 → `None`（继续下一个候选）；
- 文件存在但解析失败 → `{}` + warn（**视为"找到了但为空"**，不静默跳到下一个——
  否则用户的坏 config.yaml 会被"跳过"而看起来像"没配置"，正是批 E 治的病）；
- 存在但没有 `llm` 段 → `{}`（同上）。

### 3.2 写入（保持现状 + 明确文档）

写入方**不变**：`~/.agentnodes/llm.json`（`cli/commands/llm.py:31`、
`api/routers/system.py:19`）。理由：

- 写 config.yaml 需要重写全部写入路径（含 backup/atomic/env: 展开），
  而在**没人要求**的情况下动写入面是纯风险；
- 用户没要求"默认写 config.yaml"。

**新增**：`agentnodes config migrate` 子命令（可选，只在用户显式调用时）——
把现有 llm.json 转成 config.yaml（保留 llm.json 不动）。本批实现，
因为它是"新格式可被采用"的唯一路径。

### 3.3 边界

- **不迁移、不删除、不改写**任何用户文件（除 `config migrate` 显式调用）；
- `AGENTNODES_LLM_CONFIG` 单文件覆盖语义**不变**；
- `AGENTNODES__LLM__*` env 覆盖语义**不变**；
- 所有写入方仍写 llm.json——**quantnodes 读到的永远是它自己那份**。

---

## §4 验证

| 测试 | 断言 |
|---|---|
| 双路径回退 | 只有 `~/.quantnodes/llm.json` 时能读到（**本批核心回归**）|
| 双路径优先级 | `.agentnodes/llm.json` 与 `.quantnodes/llm.json` 都在 → 读前者 |
| config.yaml 优先 | config.yaml 与 llm.json 都在 → 读 config.yaml |
| config.yaml 单独 | 只有 config.yaml → 正常解析（含 profiles / env: 展开）|
| env 覆盖 | `AGENTNODES_LLM_CONFIG` 设了 → 只读它，忽略路径链 |
| 坏文件不静默跳过 | 存在但解析失败 → `{}` + warn，**不**回退到下一个 |
| 三处 docstring | `config.py` / `quantnodes_bridge.py` / `conftest.py` 与代码一致 |
| 现有 130+ LLM 测试 | 全绿（无行为变化）|

---

## §5 未做

1. **写入 config.yaml**：需要重写 backup/atomic/env 展开三件套，且无用户
   需求。留待"默认写入新格式"成为明确需求时。
2. **跨文件字段合并**：不做（见 §1 理由）。
3. **config.yaml → llm.json 反向迁移**：不需要（quantnodes 只读 llm.json）。
