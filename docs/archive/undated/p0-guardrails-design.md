# P0 防线设计：pre-commit 钩子 + Nightly Eval 跌幅告警

> 来源：`docs/archive/2026-10/architecture-harness-recommendations-20261008.md` §3 P0 两项（重评估后置顶）
> 紧迫度依据：两次已发生的逃逸——12 个 F821 NameError 进 main（`3bdde1d`）、15 个失败测试进 main（`b653eea`），本可被本地钩子拦截
> 范围：新增 `scripts/git-hooks/`、`scripts/check_eval_regression.py`、`evals/nightly-baseline.json`；修改 `.github/workflows/nightly-eval.yml`（本系列早前创建）
> 不做：GitHub 远端仓库设置（branch protection 需人工网页操作，见 §4）

## 1. P0-1：pre-commit 钩子

### 1.1 方案选择

| 候选 | 判定 |
|---|---|
| pre-commit framework（`.pre-commit-config.yaml`） | ✗ 需装 pip 包 + 镜像克隆，贡献者门槛高 |
| **版本化 shell hook + `core.hooksPath`** | ✓ 零依赖（git for windows 自带 sh），钩子进仓库可评审 |
| husky / node 方案 | ✗ 项目非 node 主导 |

### 1.2 拦截范围（关键决策）

只拦**致命规则**，不拦存量噪音：

```
ruff check src scripts evals --select E9,F821,F811 --quiet
```

| 规则 | 含义 | 现存量 |
|---|---|---|
| E9 | 语法错误 | 0 |
| F821 | undefined-name（本轮 12 个 NameError 全是它） | 0（已清） |
| F811 | 重定义 | 4（autoresearch 自 import——**不拦**，见下） |

**F811 现存 4 个会让钩子锁死所有人** —— 处理：钩子只拦「**改动文件**」的新增违规（`ruff check --diff` 模式不可行；用 `git diff --cached --name-only` 过滤文件清单传给 ruff）。存量 4 处在 `autoresearch.py`，只要没人改那 4 行区域就不触发（ruff 是文件级——只要 commit 涉及 `autoresearch.py` 就会报）。

**因此 F811 不进钩子清单**，钩子只拦 `E9,F821`（本轮实证的两类），`F811` 留给清扫批次（P1.5）处理完再加入。

### 1.3 交付物

```
scripts/git-hooks/pre-commit    # sh 脚本：只查 staged 的 .py 改动
scripts/install-hooks.sh        # git config core.hooksPath scripts/git-hooks（一条命令启用）
```

钩子逻辑：

```sh
files=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$')
[ -z "$files" ] && exit 0
python -m ruff check --select E9,F821 $files || exit 1
```

- 无 staged py 文件 → 直接过
- ruff 未安装 → **警告但不拦**（避免新贡献者被工具缺失卡死；CI 仍会拦）
- 只查 staged 文件 → 存量问题不阻塞无关 commit

### 1.4 验证标准

1. 构造一个含 `NameError` 的 staged 文件 → commit 被拒
2. 修复后 → commit 通过
3. 无 py 改动的 commit（纯 doc）→ 秒过

## 2. P0-2：Nightly Eval 跌幅告警

### 2.1 现状缺口

`nightly-eval.yml` 跑 `run_evals_llm.py --json` 但输出只进 stdout 日志——**scorecard 没落盘、无基线、无对比、无告警**。

### 2.2 方案

```
run_evals_llm --json > scorecard.json          # 落盘（既有 --json 输出重定向）
        │
        ▼
scripts/check_eval_regression.py               # 新增
  ├─ 读 scorecard.json（本次）+ evals/nightly-baseline.json（基线）
  ├─ 对比 mean 与 pass_rate@0.7
  ├─ mean 跌幅 > 0.02（绝对分）→ exit 1 + 详情
  ├─ 无基线 → 创建基线 + exit 0（首次建立）
  └─ --update-baseline → 显式刷新基线
        │
        ▼
upload artifact: scorecard.json（30 天，供追查）
```

**基线更新策略**（防漂移）：
- **跌幅告警不自动改基线**——防止「跌了→改基线→永远不告警」的自我豁免
- 提升或有意调整后，用 `workflow_dispatch` 输入 `update_baseline: true` 显式刷新
- 本地刷新：`python scripts/check_eval_regression.py --update-baseline`（跑完 eval 后）

**阈值**：`mean` 绝对跌幅 > **0.02**（0.957 → 0.937 即告警）。选绝对值而非相对值：score ∈[0,1]，0.02 约合 1.4 个题目的均分变化，比 2% 相对值（0.019）更直观且对高分段不迟钝。`pass_rate@0.7` 跌幅 > 0.05 作为第二触发条件（及格率是更粗但更硬的信号）。

**基线初始化**：
1. 首选：本地用 MiniMax 跑一次真实 eval → `--update-baseline` 写入 `evals/nightly-baseline.json` 并入库
2. 若本地跑不动：首次 nightly 自动建基线（脚本无基线分支），doc 记录该情况

### 2.3 workflow 改动（`nightly-eval.yml`）

```yaml
- name: Golden eval (real LLM)
  if: env.LLM != 'none'
  run: |
    ... run_evals_llm.py --json > scorecard.json   # 改：重定向落盘（保留 stderr 进日志）

- name: Check scorecard regression
  if: env.LLM != 'none'
  run: python scripts/check_eval_regression.py --scorecard scorecard.json

- name: Upload scorecard artifact          # 改：补 scorecard.json
  uses: actions/upload-artifact@v4
  with:
    path: |
      evals/golden_tasks.jsonl
      scorecard.json
```

`workflow_dispatch` 增加 `update_baseline` 输入（true 时 check 步骤改为 `--update-baseline`）。

## 3. 验收标准

| # | 项 | 通过条件 |
|---|---|---|
| 1 | hook 拦截 | 含 `NameError` 的 staged 文件 commit 被拒（本地实测） |
| 2 | hook 放行 | 修复后 commit 成功；纯 doc commit 不触发 ruff |
| 3 | 回归脚本·告警 | 构造跌分 baseline → `check_eval_regression.py` exit 1 并打印 diff |
| 4 | 回归脚本·建立 | 无 baseline → 创建 + exit 0 |
| 5 | 回归脚本·刷新 | `--update-baseline` → 写入文件 |
| 6 | workflow 语法 | yamllint / actionlint 或至少 YAML 解析通过 |
| 7 | 存量不锁 | 4 个 F811 存量不阻止常规 commit（钩子不含 F811） |

## 4. Branch Protection（需人工，超出本机范围）

以下在 GitHub 仓库 Settings → Branches → `main` 配置（**需要你的权限，我不操作远端**）：

- [ ] Require a pull request before merging（禁直接 push main）
- [ ] Required status checks：`lint`、`unit (3.10/3.11/3.12 各 shard)`、`eval-sim`
- [ ] Require branches to be up to date before merging
- [ ] Do not allow force pushes
- [ ] Require conversation resolution before merging

## 5. 与既有 CI 的关系

```
本地 pre-commit（E9,F821，staged 文件）     ← P0-1，秒级
    ↓ 推送
GitHub CI：lint（ruff 全量 warn）+ 12 分片 pytest + eval-sim   ← 既有 tests.yml
    ↓ 合并 main
Nightly：真 LLM eval + 跌幅门禁（exit 1 → run failure 通知）     ← P0-2
```

三层各管一段：钩子拦**致命语法/未定义名**，CI 拦**测试回归**，nightly 拦**智能退化**。

## 6. 验收记录（2026-10-08，实施完成）

### P0-1 pre-commit（commit `2b652f2`）

| # | 场景 | 结果 |
|---|---|---|
| 1 | staged NameError → commit | **拒绝**（F821 检出 + hook exit 1） ✅ |
| 2 | clean commit | exit 0 ✅ |
| 3 | 无 .py 改动 commit（纯 hook/doc 文件） | 秒过 ✅ |

环境备注：本机 git 为 MinGit，hook 由 `D:\tools\MinGit\usr\bin\sh.exe` 执行；`ruff` 在 PATH（3.12 Scripts），hook 直调成功。`scripts/install-hooks.sh` 供 git-bash/Linux 启用；本机已用 `git config core.hooksPath scripts/git-hooks` 直接启用。

### P0-2 eval 跟幅门禁

| # | 场景 | 结果 |
|---|---|---|
| 1 | 无 baseline | 建立 + exit 0 ✅ |
| 2 | mean -0.047 / pass -0.09 | 双 REGRESSION + **baseline 未被改写** + exit 1 ✅ |
| 3 | `--update-baseline` | 写入 + exit 0 ✅ |
| 4 | 阈值内波动（且为提升） | exit 0 + 刷新提示 ✅ |
| 5 | YAML 解析 / ruff / py_compile | 全过 ✅ |

实施中修正：`load()` 改 `utf-8-sig` 读取（PowerShell 写 JSON 带 BOM 会 exit 2——本地跑 `--update-baseline` 也会遇到）。

### 基线状态

`evals/nightly-baseline.json` **尚未建立**——按设计由首次成功 nightly 自动创建（case 1 路径已验证）。如需立即基线：本地跑一次真 eval 后 `python scripts/check_eval_regression.py --update-baseline`。
