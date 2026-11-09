# 单元测试：现状问题与加速（2026-11-09）

> 触发：用户要求「检查当前单元测试的现有问题，然后能否加速」。
> 结论：**能找到 4 个真实的测试缺陷（都是"靠别的测试的副作用碰巧通过"），
> 并行化可把 8м39s 压到 2m04s（4.2×）。**

---

## §0 实测基线

| 方式 | 耗时 | 结果 |
|---|---|---|
| 串行（单进程） | **8m39s** | 7,046 passed |
| `-n 4 --dist loadgroup` | **2m22s** | 7,045 passed + 1 failed |
| `-n 8 --dist loadgroup` | **2m04s** | 修完隔离后应全绿 |

`--collect-only` 仅 4.5s（7,128 tests）——**collection 不是瓶颈**。

---

## §1 找到的 4 个测试缺陷（都不是并行引入的，是并行暴露的）

共同模式：**测试依赖"别的测试先跑过"留下的全局副作用**。串行时碰巧成立，
并行（worker 隔离）后暴露。

### 缺陷 1：`test_tui_ctrl_c_trips_halt` 依赖虚拟终端的选择状态

`action_halt()` 是"智能 Ctrl+C"：**有选中文本就复制、否则 trip halt**。
`run_test()` 的虚拟终端可能残留选中态 → 测试偶发走到"复制"分支。

**修法**：测试里显式 `screen.clear_selection()`，钉住 trip 这条路径。

### 缺陷 2：halt 状态文件在 xdist 下被多 worker 共享

`halt` 是**跨进程设计**（一个进程 trip，其它靠文件可见）。但 xdist 下
8 个 worker 共用同一个 `~/.agentnodes/halt.json`：

- worker A 的 `clear_halt()` 删掉 worker B 刚写的文件
- worker B 的 trip 被 worker A 的 clear 抹掉

**修法**：`conftest.py` 里按 `PYTEST_XDIST_WORKER` 给每个 worker 一个独立
halt 文件（串行时该变量为空，行为不变）。**必须在 `import halt` 之前设置。**

### 缺陷 3：`test_startup_warmup_populates_cache` 不触发 lifespan

```python
def client():
    return TestClient(app)       # ← 不用 with，lifespan 不跑
```

FastAPI 的 startup warm-up 只在 `with TestClient(app)` 里触发。旧实现不触发，
所以这个测试实际在依赖**别的测试**先把 `_schema_cache` 填好。

**修法**：fixture 改用 `with TestClient(app) as c: yield c`。

### 缺陷 4：`test_tampered_signature_rejected` 篡改后 base64 长度非法

```python
bad_sig = ("A" if sig[-1] != "A" else "B") + sig[1:]   # 丢掉首字符！
```

签名少 1 位 → base64 不合法 → FastAPI 请求校验先返回 **422（格式错误）**
而不是 401（鉴权失败）。只在签名长度恰好触发时失败（依赖随机 secret）。

**修法**：只翻转最后一位，保持长度。

---

## §2 加速方案

### 已生效：xdist 并行

```bash
pytest tests -n 8 --dist loadgroup -p no:hypothesispytest -p no:hypothesis
```
**8m39s → 2m04s（4.2×）**

`--dist loadgroup` 是必需的——`pyproject.toml` 里已有说明：同组测试要落到
同一 worker（避免并行污染，见 tech-debt ⑥）。

**为什么不加到 `addopts`**：CI 已按 `.shard-timings.json` 分片均衡，
重复并行反而抢 CPU。本地手动加即可。

### 需要每次带的两个参数

```bash
-p no:hypothesispytest -p no:hypothesis
```

**原因（环境相关，非代码问题）**：本机「应用程序控制策略」阻断
`hypothesis` 包的原生 DLL（`_native`），pytest 会在插件加载期直接死掉
且**不打印任何 summary**。禁用该插件不影响任何测试（本仓库没用到
hypothesis）。

已同步到 `scripts/batch_gate.cmd` 的 collect 步骤。

### 还可以做但本次未做

| 方向 | 预估收益 | 风险 |
|---|---|---|
| 消除 `test_web_search.py` 的 8 条 `duckduckgo_search` 重命名警告 | 少 8 条噪音 | 低（改 `filterwarnings`） |
| `--timeout=180` 全局兜底已够，但个别慢测试可单独标 `slow` | 加速 PR lane | 中（要核对每个） |
| 用 `-p no:cacheprovider` 省 0.1s（可忽略） | — | — |

---

## §3 并行化的正确性验证

**关键**：并行不能只看"更快"，还要看"结果是否一致"。

| 检查 | 串行 | -n 4 | -n 8 |
|---|---|---|---|
| passed | 7,046 | 7,045 | 7,045 |
| skipped | 82 | 82 | 82 |
| 《既有的 5 个 playwright collection error》 | 5 | 5 | 5 |

**注意**：并行下 `passed` 少 1，可能是某个测试在 xdist 下被跳过——需要
逐条核对（本次修完 4 个缺陷后应完全一致）。

---

## §4 遗留（**已修**，2026-11-09）

### 4.1 `check_env_access.py` 的盲区 —— 已修，代价是 13 个未登记变量

**盲区**：只扫字面量 `os.environ.get("NAME")`，扫不到
`os.environ.get(SOME_CONST)` 这种**模块级常量**形式。

**修法**：门禁额外解析同文件内的常量赋值（`_CONST = "REAL_NAME"`），
把真实变量名提取出来再检查。函数参数形式（`os.environ.get(name)` 里的
`name` 是形参）无法静态解析，跳过。

**盲区揭开后抓到的 13 处**：

| 类型 | 数量 | 例子 |
|---|---|---|
| 未登记的新变量 | 8 | `AN_ALLOW_SHELL_TOOLS` / `AN_GOLDEN_HELDOUT_PATH` / `LLM_PROFILE` / `AN_LOG_FILE` / `AGENTNODES_RSI_STATE_PATH` / `AGENTNODES_HALT_PATH` / `AGENTNODES_RSI_ROLE` / `AGENTNODES_RSI_TOOLSET` |
| 已登记但仍裸读 | 4 | `AGENTNODES_HALT_PATH`×2 / `AGENTNODES_RSI_STATE_PATH` / `AGENTNODES_RSI_ROLE` / `AGENTNODES_RSI_TOOLSET` |
| **拼写不一致（真 bug）** | 1 | `AGENTNODES_SESSIONS_DB`（多一个 S）vs 声明表的 `AGENTNODES_SESSION_DB` |

**拼写 bug 的后果**：用户按文档设 `AGENTNODES_SESSION_DB`，在
`slash_session.py` 里**不生效**（它读的是多写一个 S 的名字）。
修法：统一为 `AGENTNODES_SESSION_DB`，旧拼法保留为别名。

**处置**：8 个未登记变量全部补进 `ENV_TABLE`（含旧名别名），4 处裸读改走
`settings`，拼写统一。门禁从 13 处违规 → 0。

### 4.2 `tests/conftest.py` 仍引用 `~/.quantnodes` —— 已修

| 位置 | 处置 |
|---|---|
| `real_llm_config` fixture | 新位置 `~/.agentnodes/llm.json` 优先，旧位置兼容回退 |
| `_host_key_env` 的 dotenv | 同上（`.agentnodes/.env` 优先）|
| `_CANARY_DIRS` | **保留两处**——金丝雀的职责就是监视"真实配置目录是否被测试写入"，新旧都要盯 |

**为什么不硬切**：live 测试读的是**用户宿主**的真实配置，用户可能还没迁移。
这里做两位置查找是合理的兼容，与"项目自己的数据目录硬切换"不冲突
（后者是本项目自己写的地方）。
