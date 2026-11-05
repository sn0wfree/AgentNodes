# Alpha Zoo YAML 求值器缺陷台账（2026-11-03）

状态：**已知缺陷，未修复**。属业务域问题，与分层重构无关，单独立项。
发现途径：W2 阶段修复 `apps/alpha_zoo/loader.py:184` 的悬空 import 后，
YAML 加载路径**首次真正生效**，随之暴露了下列潜伏问题。

---

## 背景：这个缺陷为什么长期不可见

`loader.py` 的 `compute_alpha` 有两条加载路径：

```python
# 1) YAML
if yaml_file:
    try:
        from .alpha_zoo_yaml import compute_alpha_from_yaml, load_alpha_yaml   # ← 曾是悬空 import
        ...
        return result
    except Exception:
        logger.debug("YAML load failed, trying .py fallback")

# 2) .py fallback
if py_file:
    ...
```

该 import 写成了 `from .alpha_zoo_yaml`（相对导入），解析为
`agentnodes.core.alpha_zoo.alpha_zoo_yaml`——**这个模块从不存在**
（`alpha_zoo_yaml.py` 一直位于 core 包根下，不在 `alpha_zoo/` 子包内）。

后果：**YAML 分支每次都抛 `ModuleNotFoundError`，被 `except Exception` 静默吞掉，
永远走 `.py` 回退**。408 个 YAML 定义从未被执行过，两条路径的差异因此从未暴露。

2026-11-03 W2 修复该 import 后，YAML 路径开始真正执行，缺陷随之显现。

---

## 缺陷一：`gtja191_001` 等因子经 YAML 求值产出全 NaN

### 现象

```python
from agentnodes.apps.alpha_zoo import compute_alpha
compute_alpha("gtja191_alpha_001", panel)   # → 100% NaN（YAML 路径）
```

对照 `.py` 路径同一因子：NaN 仅 1.2%，属正常。

### 定位过程

逐 step 求值（`evaluate_node(expr, env, panel)`）：

| step | 表达式 | 结果 |
|---|---|---|
| v | `{column: volume}` | DataFrame (500,50) nan=0.0000 |
| c | `{column: close}` | DataFrame (500,50) nan=0.0000 |
| o | `{column: open}` | DataFrame (500,50) nan=0.0000 |
| **x** | `rank(delta(log(where(v, gt(v,0))), 1))` | **DataFrame (500,50) nan=0.0020，但 min==max==0.51** |
| y | `rank(safe_div(sub(c,o), o))` | DataFrame (500,50) nan=0.0000，min=0.02 max=1.0（正常） |
| final | `mul(neg(1.0), ts_corr(x, y, 6))` | **100% NaN** |

关键观测：

```
x min/max: 0.51 0.51        ← 全部值恒定
x row0  nunique: 0          ← 整行无变化
x row250 nunique: 1         ← 整行仅 1 个不同值
```

`x` 退化为常数 → 滚动窗口内零方差 → `ts_corr` 全部 NaN → final 全 NaN。

### 根因

`where` 节点在 YAML 里只给了 2 个参数：

```yaml
- op: where
  args:
  - ref: v          # condition
  - op: gt
    args: [ {ref: v}, {value: 0} ]
```

而求值器走的是 `_where_df(condition, true_val, false_val)` **三参数**签名
（`alpha_zoo_yaml.py:84`），缺省第三参数时 `np.where` 的行为与预期不符，
导致 `where` 后序列退化，`delta`/`log`/`rank` 一路传递后成为常数。

同一份 YAML 对应的 `.py` 实现写的是标准语义
`np.where(volume > 0, volume, np.nan)`——两者不等价。

### 影响范围（待精确盘点）

初步扫描 408 个 YAML 文件中 `where` 出现 **108 次**，但并非每个都会退化为常量
（取决于被包裹的表达式与面板分布）。需要逐个 YAML 跑一遍、与 `.py` 版本对照，
才能给出确定的受影响清单。

已知受影响：
- `test_alpha_zoo.py::test_alpha_index_matches` — 118 个参数化用例失败（index mismatch）
- `test_alpha_zoo.py::test_alpha_nan_ratio` — `gtja191_001` 100% NaN

---

## 缺陷二：YAML 与 .py 两套实现长期分叉，缺乏一致性校验

408 个 alpha 同时存在 `.yaml` 与 `.py` 两个定义，但**没有任何测试校验两者等价**。
YAML 路径此前从未真正执行，等于一半的实现从未被验证。

建议增加一个对照测试：对每个 alpha，分别用 `.yaml` 和 `.py` 计算，
断言结果数值等价（`np.allclose`，允许 NaN 位置一致）。
这样任何一侧的实现漂移都会立刻暴露。

---

## 建议修复方向（未实施）

1. **先确定 `where` 的语义契约**：YAML AST 里 `where` 到底是
   - `where(condition, true_value)` → 隐式 `false_value = NaN`？
   - 还是必须显式三参数？
   现状是两参数在 108 处使用、三参数签名实现，两者不匹配。
   `_where_df` 应支持二参数形式，缺省 `false_val=np.nan`。
2. 修完后逐个 alpha 跑 YAML 路径，与 `.py` 版本数值对照，列出仍不一致的清单。
3. 落一个参数化对照测试（缺陷二的建议）作为长期防线。
4. 在 `loader.compute_alpha` 的 YAML→.py 回退处，把 `logger.debug` 提升为
   `logger.warning`（或计数上报）——两条路径结果差异应当可观测，
   而不是像过去那样完全无声。

---

## 与分层重构的关系

**无关**。本次分层迁移只是把文件从 `core/` 移到 `apps/`，未改变任何求值逻辑。
缺陷一是由修复一个**既有悬空 import**（迁移过程中被 `verify_residue.py` 扫出的 8 处之一）
而顺带暴露的，迁移本身没有引入它。

迁移侧已完成的正确动作：
- `apps/alpha_zoo/loader.py:184` 的 `from .alpha_zoo_yaml` → 正确的绝对路径
- 该修复使 YAML 路径恢复可用（这是对的，不应回退）

后续若要修缺陷一，动的是 `apps/alpha_zoo_yaml.py` 与 `apps/alpha_zoo/loader.py`
的求值/回退逻辑，与层结构无关。
