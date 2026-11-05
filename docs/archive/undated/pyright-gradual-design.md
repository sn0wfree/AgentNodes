# Pyright 渐进类型债治理

> 来源：`refactor-plan-20261008.md` Phase 4-备选 · `architecture-harness-recommendations-20261008.md` §3 P2
> 现状：**无 pyright 配置、未装机、pyproject 无类型检查** —— 类型债零治理
> 目标：**渐进倒计时**（每 PR 顺手修几处，错误数单调下降），不是「一次修完」
> 范围：`pyrightconfig.json` + `pyproject.toml`（可选）+ CI lint job 集成

## 1. 动机（为什么值得做）

| 缺口 | 类型债的代价 | pyright 的价值 |
|---|---|---|
| mixin/`self.*` 隐式耦合 | 12 个 F821 NameError 的根源（Step 2 教训） | 类型检查抓「漏传/未定义」类 bug |
| API 面无契约 | 签名变更静默失败 | 参数/返回类型标注强制 |
| 重构护栏 | 轨迹 diff 是行为面，类型是结构面 | 双层护栏（行为+结构） |

## 2. 配置（宽松模式起步）

`pyrightconfig.json`：

```json
{
  "include": ["src"],
  "exclude": ["**/__pycache__", "src/strategy_research/core/alpha_zoo", "src/strategy_research/core/study/_archived", "src/strategy_research/templates"],
  "typeCheckingMode": "basic",
  "reportMissingImports": false,
  "reportUnknownMemberType": false,
  "reportUnknownArgumentType": false,
  "reportUnknownVariableType": false,
  "reportUntypedFunctionDecorator": false,
  "reportUntypedClassDecorator": false,
  "reportPrivateUsage": false
}
```

**宽松原则**（避免一次被错误淹没）：
- `basic` 而非 `strict`（少 ~70% 噪音）
- 关闭 unknown-type 类（mixin/`Any` 大量存在）
- 关闭 private-usage（测试 patch 私有字段常态）
- 保留 `reportUndefinedVariable`（= F821 类，价值最高）
- `alpha_zoo`/`_archived`/`templates` 排除（存档/模板不修）

## 3. CI 集成（渐进不拦截）

`tests.yml` lint job 加 pyright 步骤：

```yaml
- name: Pyright (observe-only)
  run: |
    python -m pip install pyright
    pyright --outputjson > pyright-report.json || true
    python scripts/pyright_regressions.py --report pyright-report.json
```

`scripts/pyright_regressions.py`：
- 读 report，对比基线 `docs/pyright-baseline.json`（记录错误数）
- **只在错误数增加时 exit 1**（regressions-only，不卡存量）
- 基线更新显式（`--update-baseline`，同 eval 基线哲学）

## 4. 渐进路线

| 阶段 | 目标 | 触发 |
|---|---|---|
| v1（本轮） | 装机 + 宽松配置 + 基线 + CI observe-only | 首发 |
| v2 | 错误数不增（regressions-only 卡 CI） | 基线建立后 |
| v3 | 逐模块 strict（先新代码/后改到的 mixin） | 错误数 < 50 后 |
| v4 | `typeCheckingMode=strict` + pyproject 标注 | 长期 |

## 5. 验收

| # | 项 | 条件 |
|---|---|---|
| 1 | 装机 | `python -m pip install pyright` 成功（node 二进制下载） |
| 2 | 配置 | `pyrightconfig.json` basic 宽松模式 |
| 3 | 首跑 | 错误量报告（数量分级：F821 类 vs annotation 类） |
| 4 | 基线 | `docs/pyright-baseline.json` 记录错误数 |
| 5 | CI | lint job observe-only 步骤（exit 0 不拦截） |

## 6. 风险

- pyright 需下载 node 二进制（网络受限）→ 若装机失败，记档改期
- 错误量大 → 分级报告（优先修 undefined-variable 类，annotation 类渐进）
