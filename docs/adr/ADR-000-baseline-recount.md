# ADR-000：依赖门禁计数口径修正 + 基线复算 + 接缝触点登记

日期：2026-11-03　状态：已接受　Owner：平台架构 Owner
关联：docs/layered-skeleton-design-20261103.md §2.4.1/§7 · tests/test_layer_matrix.py

## 背景

v7.2 归层（core/tools→L2、core/registry→L4）后未复算门禁。2026-11-03 实跑暴露两处：

1. **计数口径缺陷**：`test_layer_matrix` 对相对导入按 module+alias 各计一次——
   `from ...tools.data_clean import PRESETS` 计 2。双计口径下 L3→L2=102（图面 92、
   基线 92 均失真），物理 import 语句仅 5 处（data_clean_tools.py:85,98 ·
   factor_tools.py:28,47,328）。
2. **接缝白名单缺口**：study/monitor.py:13、study/store.py:19 →
   `observability.logging_setup.dual_log`（2026-11-02 日志轮引入）未登记，
   `test_study_platform_touchpoints_are_whitelisted` 红。此前「103 失败=环境竞争」
   的归因据此修正。

## 决定

1. **计数器改为物理口径**：每条 import 语句计 1；同节点多 alias 指向同层去重。
   理由：双计无语义价值，物理语句数才可审计、可与 grep 对账。
2. **基线重置（物理口径）**：L2>L1 1 · L3>L1 2 · **L3>L2 46** · L4>L2 2 · L4>L3 4，
   合计 **55**（旧双计 107 作废）。G2a = 9 语句（簇 B/C），G2b = 46 语句。
3. **接缝触点登记**：STUDY_PLATFORM_TOUCHPOINTS 增 `monitor.py:{observability}`、
   `store.py` 增 `observability`——理由：dual_log 是日志统一入口（研究域用平台
   可观测机制属合法服务使用），登记于 seam-contract 文档。

## 影响

- 图 v8 ⑦ 矩阵/簇数字按物理口径刷新（v8.3）。
- W1-3 审计对象 = 46 条语句（非 102）；W2-3 ADR-003 以 46 为处置母体。
- 门禁断言不变：只降不升；G2a/G2b 落地时各自下修。

## 验证

`pytest tests/test_layer_matrix.py tests/test_architecture_gates.py -q` 全绿
（2026-11-03 实跑：2 passed + 101 passed 含相关组）。
