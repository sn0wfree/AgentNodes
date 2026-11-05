# CI 分片与 Flaky 治理策略

> 适用版本：v0.6.0+
> 维护：CI / Eval 工作组

## 1. 背景

`tests/` 当前 544 个文件、12,366 个 case，单进程跑一次 ~5–10 分钟。
GitHub Actions 单 job 上限 6 小时，PR feedback 窗口 30 分钟内必须有结果。
矩阵策略：**4 分片 × 3 Python = 12 个 unit job**（lint 与 eval-sim 另算）。

## 2. 分片算法

`scripts/pytest_shard.py` 提供两种模式，由 `--mode` 选择：

| 模式 | 何时用 | 行为 |
|---|---|---|
| `round-robin`（默认） | 第一次跑、缺 timing 数据 | 按文件名排序后 mod 4 取第 N 桶。**稳定但时间不均衡**：大文件被压在同一桶时会拉跨那个 shard |
| `time-aware` | 已经收集过 `.shard-timings.json` | LPT 贪心装箱（最重文件先入桶 + 入当前最轻桶），保证预估耗时均衡 |

**为什么不用 `pytest-split`（已装但不强求）？** pytest-split 是按测试函数切而非文件，对慢函数更细；但 LPT 文件级够用且不依赖额外 plugin 协议。如果未来分片数 > 8 或希望函数级均衡，再切到 pytest-split。

## 3. 生成 timing 基线

```bash
# 一次性，跑全量 (5-10 分钟)
$py scripts/collect_shard_timings.py --root tests --output .shard-timings.json
```

输出形如：

```
wrote .shard-timings.json — 543 files, total 312.4s
  (max 18.7s, median 0.4s)
```

写完 commit 到仓库根即可。CI 默认走 `round-robin` 模式（不读 timings）以保证稳定性；本机调试时用 `--mode time-aware` 验证均衡。

## 4. CI workflow

`.github/workflows/tests.yml` 的 `unit` job：

```yaml
- run: |
    python -m pytest $(tr '\n' ' ' < /tmp/shard_files.txt) \
      -q --tb=short --reruns=2 --reruns-delay=1 --durations=20
```

参数语义：

| 标志 | 作用 |
|---|---|
| `--reruns=2` | 单 case 失败最多重试 2 次。**flaky 不会阻塞门禁**，但仍上报失败 |
| `--reruns-delay=1` | 重试间隔 1 秒，缓解「前一个测试把端口/文件占住」类时序问题 |
| `--durations=20` | 跑完后打印最慢 20 个 case 到 stderr |

artifact 上传：

- `tui-screenshots-py{ver}-s{shard}` — 已有（TUI 截图）
- `pytest-report-py{ver}-s{shard}` — 新增，`.pytest_cache/v/cache/lastfailed` + `nodeids`，失败时供 `pytest --lf` 复跑

## 5. Flaky 治理

### 5.1 临时兜底

`--reruns=2` 让偶发失败不阻塞 PR，但**不是免死金牌**：

- 同一 case 3 次都失败 → 真 bug，按常规流程修
- 同一 case 第 1-2 次失败、第 3 次过 → 记到 `.known-flaky.md`，下文流程处理

### 5.2 已知 flaky 名单

`tests/_fixtures/known_flaky.py`（未来建）放显式 flaky 标记：

```python
import pytest
flaky = pytest.mark.flaky(retries=2, condition=lambda: ...)
```

目前 `test_session_triggers.py` 因历史 flake 太多直接从 shard 中**排除**（见 `pytest_shard.py::DEFAULT_SKIP`），改在 nightly LLM eval job 单跑。

### 5.3 根因修

收到 3 次以上同一测试 flaky 信号后，按这个顺序查：

1. **时序**：sleep 时间不够 → 改用 polling / `eventually()`
2. **共享状态**：port / file / DB → 用 fixture scope 隔离
3. **环境敏感**：`time.time()` / `os.urandom()` → mock 或注入
4. **CI only**：Windows / Linux 差异 → 用 `pytest.mark.skipif` 显式标

## 6. 迭代循环

```
1. 跑全量 collect → 生成 .shard-timings.json（基线）
2. CI 跑通 → 看 shard 耗时是否均衡
3. 不均衡 → 调整 .shard-timings.json（如手动把超慢文件标大值）→ 重跑
4. 出现 flaky → 收信号 → 修根因 / 加重试 / 排除
5. 新增/删除测试文件 → 跑一次 collect 刷新基线
```

## 7. 已知 Caveat

- **Windows 跑 collect_timings 慢**：12K 测试 ~10 分钟；建议在 Linux 跑或周末跑
- **大测试文件不能拆**：例如 `test_study_*` 几个文件单跑 30s+，分片只能让其他 shard 更快
- **新增测试**：CI 默认 round-robin，不依赖 .shard-timings.json；time-aware 是本地调试
- **分片数变更**：改 `--shards` 后必须重跑 collect；否则历史 timings 漂移

## 8. TODO

- [ ] `tests/_fixtures/known_flaky.py` + 把 `test_session_triggers.py` 切到那里
- [ ] night job 加 `pytest --lf` 复跑 lastfailed，作为 flaky 二次确认
- [ ] 分片数 4→6 评估（等 12K+ 增长再说）
- [ ] PR 评论自动贴「最慢 5 个 case」与「flaky 列表」
