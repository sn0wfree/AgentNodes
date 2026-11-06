# 下一步：先落文档，再进架构讨论

## 立即动作（本轮唯一要做的事）

**把刚才的评估与方案写成设计文档**，位置 `docs/design/goal-capability-merge-design-20261105.md`，与既有的 `docs/design/layer-attribution-review-20261105.md` 同级同风格（中文、含实测数字表、每条结论都能追溯到具体文件行号）。

文档结构：

| 节 | 内容 |
|---|---|
| §0 结论摘要 | 三条判断：autoresearch 本就是 goal 的拓扑配置；恢复 goal = 给 RSI 补细粒度证据源；精简的正确边界是端口化注入而非跨层基类 |
| §1 现状实测 | 三个子系统的归档真值表（goal 6,147 行在档案 / study 37 文件被直删 / autoresearch 同）、kernel 侧 `SwarmPreset` 仍保留 goal 字段、`resolve("goal.*")` 三处必然抛错的实测、`tool.goal` 5 个工具的可见性与失败路径 |
| §2 三个子系统的历史关系 | goal（整合版）→ study（切出去的旧版）→ autoresearch（拓扑）的演进链 + 批次 2/3 的搬运与删除证据（`08303f04` / `411a7ee7` / `bd3f6009`）|
| §3 自我进化闭环的信号缺口 | `rsi_wiring.py:211` 四个源同指 `EventStoreReportSource`；journal 的四个事件 kind 里 `study.round_done` 永空；scoreboard 只是 status 计数。对照 goal 侧 archive 里的 journal API 与 `LeverScoreboard` |
| §4 chat 耦合面 | 已预埋的 5 处（`chat_loop.py:41` / `service.py:942` / TUI ctrl+G / `tool.goal` / `context_injector`）+ 缺的 3 处（slash 命令 / SSE 载荷 / API 端点）|
| §5 合并后的目标形态 | `apps/goal/` 目录树（ledger / orchestration / executors / presets）、4 个 `goal.*` kind 的注册表、kernel 零改动的论证 |
| §6 三阶段方案 | R1 恢复能力闭环 · R2 chat 全套接线 · R3 preset 化 + RSI 证据源接入；每阶段含具体文件与验证表 |
| §7 原批次处置的修正 | D1 反转、A2 的 `GoalError` 保留、D2 仍归档、B1 不变 |
| §8 风险与已知代价 | 量化味回流、6,147 行单批次、DB 路径冲突、schema_port 退化 DDL 交互 |
| §9 未做的事 | 不做跨层账本基类抽象的理由（H3 三重保证）|

**文档写完即交付，不动任何代码。** 写完在对话里给出摘要，然后转入架构讨论（goal / evolution / chat 三者的分层边界与依赖方向），暂不执行 R1–R3。

## 文档必须满足的校验

- 所有路径引用可解析（写完跑一遍引用解析脚本，0 悬空）
- 所有行数/文件数断言与实际文件逐一核对
- 每条「已删除 / 已保留」的断言都能追溯到一条 `git log` 记录或一个实测输出

## 后续（不在本轮）

架构讨论产出结论后，再按 R1 → R2 → R3 执行；原批次 A2（`errors.py` 的 `GoalError` 保留、`BacktestError` 移除）与 B1（`validator.py` / `grounding.py` 研究语义清理）在 R1 之后并行处理。
