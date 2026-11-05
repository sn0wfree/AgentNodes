# 长任务处理（后台化 + 日志轮询）

> 适用：任何预计耗时的命令 / 回测 / 计算 / 下载。核心原则：
> **日志在推进 = 任务正常（跑多久都行）；日志停滞 = 卡死，交给系统处理。**
> 永远不要阻塞等待一个长任务 —— 用后台化 + 观察窗轮询。

## 何时该转后台（主动确认）

出现以下信号时，**显式**使用后台化（不会自动发生，必须主动传参）：

- 命令可能超过 120 秒（工具超时上限）：大数据下载、训练、批量计算
- `run_backtest` 前台执行超时失败 → 错误提示后，**重试时加 `background=True`**
- 计算量明显大：全市场扫描、长历史回测、大参数网格

**判断错误也没关系**：前台超时 → 提示 → 加参数重试，不浪费。

## 两种启动方式（任选其一）

### 1. 回测后台化：`run_backtest(background=True)`

```json
→ {"status": "running", "task_id": "bg_xxxx", "log": "bg_tasks/xxx.log",
   "fix": "轮询: run_bg_command(action='wait', task_id=...) 观察窗 ×3"}
```

### 2. 任意命令后台化：`run_bg_command(action="start", command="...")`

```json
→ {"status": "running", "task_id": "bg_xxxx", "log": "bg_tasks/xxx.log", "pid": N}
```

## 轮询协议（观察窗 × 最多 3 次）

```
run_bg_command(action="wait", task_id="bg_xxxx", seconds=15)
  → {"state": "running", "tail": "最后3行..."}   → 有新内容 = 正常，继续观察
  → {"state": "stalled", "stalled_seconds": N}   → 停滞，见下
  → {"state": "done", "exit_code": 0, "result": {...}} → 完成，正常继续
```

| 规则 | 值 |
|---|---|
| 观察窗 | `wait` 15 秒一次，**最多 3 次**（约 45 秒） |
| 每次读取 | 日志尾部 ≤ 20 行（`log` action），控制 token |
| 有进展 | 继续下一次 `wait`（日志在涨，任务正常） |
| 3 次无进展 | **停止轮询**。要么 `kill` 主动放弃，要么在回复中报告"后台任务停滞，交由系统 watchdog 处理"——绝不在 3 次后继续无限轮询 |
| 完成 | `exit_code=0` → 读 `metrics.json` / `run_card.json` 继续；`exit_code≠0` → 读日志尾部诊断 |
| 停滞 | `state="stalled"` → 同"3 次无进展"处理（kill 或交回 watchdog） |

## 硬性纪律

- **不重复启动**同一任务（task_id 失效 = 任务已结束，重新 start 前先确认没在跑）
- 观察窗之间不需要额外 sleep（`wait` 内部已等待）
- 每轮只处理自己的 task_id；不 kill 别人的任务
- 日志每次读取 ≤ 20 行；尾部有 `[backtest]` 进度行 = 引擎在推进
