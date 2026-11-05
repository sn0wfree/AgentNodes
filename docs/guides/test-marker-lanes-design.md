# P1 设计：测试 Marker 三层化

> 来源：`docs/archive/2026-10/architecture-harness-recommendations-20261008.md` §2.2（P1）
> 问题：12,366 测试无语义分层——分类靠人记（`test_session_triggers.py` 硬编码在 shard 脚本、`test_chat_real_llm` 混在日常跑）、`--reruns` 全量兜底导致 flaky 信号被噪声淹没
> 范围：`pyproject.toml`（注册 markers）、`tests/conftest.py`（集中白名单打标）、`.github/workflows/tests.yml`（PR lane 过滤）
> 状态：设计定稿，待实施

## 1. 三层 Lane

| Lane | 触发 | 选择器 | 目标时长 |
|---|---|---|---|
| **PR gate** | 每个 PR / push | `-m "not llm and not e2e and not slow"` | < 5 min（按分片并行） |
| merge 后全量 | push main | 无过滤（含三类） | < 15 min |
| nightly | 定时 | `llm` 类 + 真实 eval | 不限 |

**默认安全原则**：只把**确定的**文件列入三类白名单；漏分类 = 多跑（无害），误分类 = 测试从 PR gate 消失（有害）。所以白名单**宁窄勿宽**，每条目在 §3 附理由。

## 2. 实现：集中白名单 + 自动打标

**不用** 544 个文件手工打标（工作量大且每加测试都要记得打），**用** `tests/conftest.py::pytest_collection_modifyitems` 按**文件名模式**集中匹配：

```python
# tests/conftest.py
LLM_FILES = {"test_chat_real_llm.py", "test_minimax_search.py", "test_web_search.py"}
E2E_FILES = {"test_chat_e2e_playwright.py", "test_e2e_chat.py", "test_sse_e2e.py",
             "test_goal_e2e.py", ...}
SLOW_FILES = {"test_dead_code_no_imports.py", "test_bg_proc.py", ...}  # 见 §3

def pytest_collection_modifyitems(items):
    for item in items:
        name = Path(str(item.fspath)).name
        if name in LLM_FILES:    item.add_marker(pytest.mark.llm)
        if name in E2E_FILES:    item.add_marker(pytest.mark.e2e)
        if name in SLOW_FILES:   item.add_marker(pytest.mark.slow)
```

`pyproject.toml` 注册三个 marker（消 PytestUnknownMarkWarning）：

```toml
markers = [
    "llm: needs a real LLM/network (excluded from PR gate)",
    "e2e: end-to-end across process/browser boundaries (excluded from PR gate)",
    "slow: >~5s wall time (excluded from PR gate; see .shard-timings.json)",
]
```

**优点**：分类一处可审、新测试默认进 PR gate、白名单变更走 PR review。

**已知限制**：文件级粒度（同文件内混合快慢测试时整文件归类）。现状白名单文件都是整文件性质明确的，暂不支持函数级（需要时用 `item.nodeid` 前缀匹配扩展）。

## 3. 白名单初版（每条附理由）

### `llm`（真 LLM / 真外网）

| 文件 | 理由 |
|---|---|
| `test_chat_real_llm.py` | 名称即事实：真 LLM 调用（18.4s） |
| `test_web_search.py` | DDGS 真外网查询 |
| `test_minimax_search.py` | 真 MiniMax search API |

### `e2e`（跨进程/浏览器/完整服务栈）

| 文件 | 理由 |
|---|---|
| `test_chat_e2e_playwright.py` | 浏览器驱动 |
| `test_e2e_chat.py` | 完整 chat 栈 |
| `test_sse_e2e.py` | SSE 端到端 |
| `test_goal_e2e.py` | goal 全链路（6.7s） |
| `test_study_e2e_api.py` / `test_study_e2e_integration.py` | study 全链路 |
| `test_p3_integration.py` / `test_integration.py` | 多子系统集成（15.4s，同时归 slow？——**归 e2e 即可**，标签互斥非必须，同文件可多标） |

### `slow`（timings ≥5s，来源 `.shard-timings.json` 实测）

| 文件 | 实测 |
|---|---|
| `test_dead_code_no_imports.py` | **75.2s**（全库最慢：AST 全仓扫描） |
| `test_bg_proc.py` | 19.3s |
| `test_backtest.py` | 16.9s |
| `test_decide_wired.py` | 16.3s |
| `test_cli_init.py` | 9.4s |
| `test_llm_retry.py` | 8.4s |
| `test_mcp_data_tools.py` | 7.2s |
| `test_backtest_metrics_contract.py` | 6.2s |
| `test_config_runner_import.py` | 6.0s |
| `test_data_readiness.py` | 5.8s |

（实施时按实测 ≥5s 完整扫一遍 timings 补齐，本列表为设计时 top。）

**不动**：`test_session_triggers.py` 的 shard 硬编码排除（那是 sharding 关注点，与语义 lane 正交；后续可改为 `slow` marker 后统一）。

## 4. CI 改动（`tests.yml`）

`unit` job 的 pytest 调用追加 marker 过滤：

```yaml
python -m pytest $(...) -q --tb=short --reruns=2 --reruns-delay=1 --durations=20 \
  -m "not llm and not e2e and not slow"
```

- 12 个分片 job 都加同一过滤 → PR gate 只跑 fast 集
- `eval-sim` job 不变
- **merge 后全量**：push main 触发同 workflow——为区分，加 job 条件？**简化**：第一版 PR 与 push 同过滤（主防反馈时长）；全量跑移到 nightly 已有 pytest 面（或后续加 `if: github.event_name == 'push'` 不过滤的 job）——**本文档记为 follow-up**，不阻塞第一版

## 5. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | marker 注册 | `pytest --markers` 列出 llm/e2e/slow，无 UnknownMark 警告 |
| 2 | 打标正确 | `pytest --collect-only -m llm`（e2e/slow 同）= 白名单文件集合 |
| 3 | PR 过滤有效 | `pytest -m "not llm and not e2e and not slow" --collect-only` 数量 < 12,366，且不含白名单 node |
| 4 | 分层数量 | 记录三类各多少 test（预期 llm ~几十、e2e ~百级、slow ~百级） |
| 5 | 过滤后可跑 | PR 选择器实跑一轮 0 failed（有旧 flaky 记录者除外，见 §3.3 A/B 判定流程） |
| 6 | 现有排除不回归 | `test_session_triggers` 行为不变 |

## 6. 后续（本文档不做）

- push main 的无过滤全量 job（§4 follow-up）
- 函数级打标（§2 限制）
- `slow` 与 `pytest_split`/sharding 的联动（替代 `DEFAULT_SKIP` 硬编码）

## 7. 验收记录（2026-10-08，实施完成）

| # | 项 | 结果 |
|---|---|---|
| 1 | marker 注册 | `pytest --markers` 列出 llm/e2e/slow ✅ |
| 2 | 打标 collect | llm=24 / e2e=69 / slow=376（test 数；白名单文件展开） ✅ |
| 3 | PR 过滤数量 | **11904 / 12373**（排除 469 tests） ✅ |
| 4 | 过滤实跑 | 69 回归核心集 + 过滤 → **1162 passed / 0 failed / 117 deselected / 64s**（无过滤 143s——**时长减半**） ✅ |
| 5 | session_triggers | shard 脚本未动，行为不变 ✅ |

### 对设计的两处实测修正（以数据为准）

1. `test_minimax_search.py` **移出 llm 白名单**——实测全 monkeypatch（0.3s），不是真网络；doc §3 初稿有它，按「宁窄勿宽」剔除。
2. `e2e` 白名单**只收重的**——13 个 `*_e2e*` 文件实测 0.01–3.2s（mock 实现），排除它们买不到时长还丢覆盖。最终只收 6 个真跨边界/重集成文件。

### 白名单最终版

- llm（2）：`test_chat_real_llm` · `test_web_search`（live DDGS + skipif HAS_NETWORK）
- e2e（6）：playwright · goal_e2e · study_e2e_api/integration · integration · p3_integration
- slow（16）：实测 ≥5s（头名 `test_dead_code_no_imports` 75.2s）

### 后续（§6 不变）

push main 无过滤全量 job、函数级打标、sharding 的 `DEFAULT_SKIP` 迁移。

## 8. Follow-up 落地（2026-10-08）：push main 全量 job

§4 的 follow-up 已实现——同一 unit job 按 event 分流：

- **pull_request**：`-m "not llm and not e2e and not slow"`（快车道，12 分片）
- **push to main**：无过滤（含 llm/e2e/slow 全量；`test_alpha_zoo` 418s 归 LPT 后的最重 shard，merge 后无反馈时限故可接受）

实现：`Run tests` step 内按 `github.event_name` 组装 FILTER 变量，12 分片矩阵不变（无额外 job、无 yaml 分叉）。nightly 仍独立负责真 LLM eval（llm lane 的 pytest 面也在此全量里跑）。


---

## 9. 本地验证矩阵（2026-11-01）：为什么必须分进程

### 事故：混合全量制造假回归

为「跑完整 e2e」而去掉 `-m "not e2e"`，把 e2e 与单元测试放进**同一进程**，
结果：

| 现象 | 实测 |
|---|---|
| 单元全量（正确命令） | 393s |
| 混合全量 | 694–878s，最坏一次 **10986s（3 小时）** |
| 假回归 | 5 个：`test_study_e2e_integration`×4、`test_webui_e2e`×1 |
| 排查成本 | 约 1 小时（8 次全量 + 二分） |

这 5 个用例单独跑、成对跑、按文件跑**都通过**，只在同进程混跑时失败：
`AutoresearchRunner` 构造会 `open_goal_store()` 打开真实 DB；`test_webui_e2e`
驱动 live uvicorn 与 `test_webui_api`（基线中长期失败）共享 app 单例。

**结论：lane 白名单不是分类标签，是进程隔离契约。** 跨 lane 混跑会破坏它。

### 正确做法：scripts/run_test_matrix.py

复刻 CI 的做法，按文件分片、每片独立进程并行：

```
python scripts/run_test_matrix.py                    # 全部 lane
python scripts/run_test_matrix.py --lane pr --shards 4
python scripts/run_test_matrix.py --lane pr --cov --shards 1   # 覆盖率需单分片
```

实测（12 核机器）：

| lane | 时间 | 结果 |
|---|---|---|
| pr（4 分片并行，含 --reruns=2） | 179s | c1_gate clean（61 失败全在基线） |
| e2e | 245s | 84 passed |
| browser（SR_RUN_BROWSER_TESTS=1） | 12s | 10 passed |
| **合计** | **约 7.5 分钟** | 三层全覆盖 |

### 为什么不用 pytest-xdist

试过。`-n 4` 能把 PR 门压到 120s，但同进程并发破坏了依赖隐式导入顺序的
用例，失败集不稳定。文件级分片的**进程隔离**才是正确解法——CI 早在
2026-10-08 就选了这条路。xdist 已卸载，不进依赖。

它暴露的那个 bug 独立成立：`test_cli_server_cmd.py` 的 6 个用例 patch
`strategy_research.webui.routes.router`，而该模块在 W3 结构债清理中已删除
（api/app.py:240）。串行能过纯属别的测试先导入过的副作用，patch 的
`router` 变量从未被断言。已删除该残留 patch。

### scripts/pytest_shard.py 的 --exclude

成本模型不知道 `-m` 过滤：`test_alpha_zoo.py`（418s，标记 slow）会独占
一个分片，随后被 `-m "not slow"` 全部反选，该 worker 空转。
`run_test_matrix.py` 从 conftest 的 lane 白名单自动生成排除项，避免两处漂移。

### 纪律

- **不要**跨 lane 混跑（`pytest tests` 不带 `-m`）
- **不要**在迭代循环里每次带 `--cov`（额外 30–50%）；攒完改动跑一次
- 失败集判定一律用 `scripts/c1_gate.py <合并日志>`，不看单次数字
