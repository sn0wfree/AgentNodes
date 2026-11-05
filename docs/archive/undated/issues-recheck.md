# 项目问题清单（再梳理）

> 2026-09-29 · 全库复查  
> 前次：code-map / feature-merge / agent-architecture  
> 本篇：**当前仍存在**的问题分级与建议

---

## 0. 总览

| 类 | 已解决（近期） | 仍存在 |
|---|---|---|
| 运行时 | CLI 双注册、预算、toolset、bg/Windows、子任务白名单 | 流式 usage 边角、C901 巨石 |
| 测试 | 200+ 新单测，核心 183 过 | Windows chmod/CRLF 3 处、全套件偏慢 |
| 结构 | 版本号 0.6.0 对齐 | templates 双份、双前端、双事件桥 |
| 产品 | 真 LLM eval 0.96 | CLI 28 命令仍可合并 |

---

## 1. 阻塞 / 高优先（影响正确性或维护）

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| H1 | `loop.py` 111KB 上帝对象（C901×多处） | `agent/loop.py` | 继续 A5：拆 `_execute_tool_call_core` |
| H2 | `session/service.py` 92KB + `autoresearch.py` 75KB | api/core | 按职责切模块 |
| H3 | 根 `templates/`（37 文件）vs `src/.../templates`（68）漂移 | 仓库根 | **删根副本**，只留打包源 |
| H4 | 三套 backtest models | core/utils/engine | 合一 |
| H5 | CLI 28 入口（serve×3 / backtest×3 / check×3） | cli | feature-merge W1 |

---

## 2. 中优先（架构一致性）

| # | 问题 | 说明 |
|---|---|---|
| M1 | 双前端：Jinja HTMX `/webui` + React SPA | 删 HTMX |
| M2 | 双事件桥：`events.py` / `event_bus_v2.py` 已标 deprecated 仍在 | 删或移 `_archived` |
| M3 | phase_engine vs langgraph_engine | dag 假选项 |
| M4 | chat_loop vs role_factory 两套 AgentLoop 构造 | 收敛 AgentExecutor |
| M5 | alpha_zoo 新旧 loader 四套 | 只留 `loader.py` |
| M6 | `study/_archived/` 仍占仓 | 确认无引用后删 |
| M7 | eval `tokens` 偶发 0（流式 usage 不稳） | 估算回退已在 eval；根治 stream usage |

---

## 3. 低优先 / 卫生

| # | 问题 |
|---|---|
| L1 | Windows 测试：chmod `438≠384`、`list_files` CRLF 长度 |
| L2 | 根 `UNUSED` 死端点清单（已 ignore）— 可并入 docs |
| L3 | `analysis/` 调试脚本（已 ignore） |
| L4 | `pytest.mark.xdist_group` 未在 pyproject 注册 |
| L5 | 全量 `pytest` 在本机 >10min（未用并行/分片） |
| L6 | `run_bg_command` 仍含 `bash` 风格语义（已平台包装，复杂命令仍脆） |

---

## 4. 评测与质量门

| 项 | 状态 |
|---|---|
| golden evals | **mean 0.96 / pass@0.7 = 1.0** |
| 单元测试 | 核心子集 183 过 |
| 未门禁 | lint 仅手动；CI 未强制 golden eval |

建议：CI 增加 `python evals/run_evals.py`（模拟）+ 抽样真 LLM 夜跑。

---

## 5. 推荐动作（按性价比）

1. **删根 `templates/`**（H3，纯减法，半天）
2. **CLI 合并 serve/backtest/check**（H5/M 级，产品可感知）
3. **删 deprecated 事件桥 + HTMX**（M1/M2）
4. **backtest models 三合一**（H4）
5. **loop.py 再拆一刀**（H1，测试保护下）
6. **CI 加 eval 门禁** + 修 L1 Windows 断言

---

## 6. 一句话

> 功能与 agent 运行时已可用且有评测网；**剩余问题几乎全是结构债**（巨文件、双份实现、入口过多），不是算法或业务缺口。
