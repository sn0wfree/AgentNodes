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

## §4 遗留

- 上述 4 个缺陷已修，但它们的**共同根因**（依赖全局副作用的测试设计）
  没有系统性防线。建议：新增测试若依赖进程级全局（模块级 dict / 文件 /
  环境变量），必须在 fixture 里显式重置，而不是依赖执行顺序。
- `check_env_access.py` 的盲区：只扫字面量 `os.environ.get("NAME")`，
  扫不到 `os.environ.get(SOME_CONST)`（`AGENTNODES_HALT_PATH` 因此逃过检查）。
