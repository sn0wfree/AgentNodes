# QuantNodes-Research 功能演示

## 演示案例：A股20日动量因子研究

### 案例概述
研究20日动量因子（close / ts_mean(close, 20) - 1）在A股市场的表现，
从因子分析到策略创建的完整流程。

---

### 演示步骤

#### 1. 启动服务
```bash
cd /home/ll/Public/strategy-research
python -m agentnodes serve --host 127.0.0.1 --port 8783
```

#### 2. 创建会话
打开浏览器访问 `http://127.0.0.1:8783`

#### 3. 因子分析（用户输入）
```
帮我分析20日动量因子在A股的IC表现，使用10只核心A股
```

**LLM 执行流程：**
1. `get_market_data` → 获取10只A股行情
2. `import_data` → 导入DuckDB
3. `factor_cross_sectional_analysis` → 横截面IC分析
4. `factor_quintile_returns` → 五分位收益
5. `factor_ic_decay` → IC衰减分析
6. `factor_turnover` → 换手率分析

**预期输出：**
- IC均值、IR、IC>0占比
- 五分位收益表
- IC衰减曲线
- 换手率统计

#### 4. 策略创建（用户输入）
```
基于这个因子创建一个完整的策略配置，调仓频率5天，做多top10
```

**LLM 执行流程：**
1. `write_file("strategies/momentum_20d/strategy.py")` → 创建策略
2. `write_file("strategies/momentum_20d/config.yaml")` → 创建配置
3. `run_backtest(strategy_name="momentum_20d")` → 执行回测

**策略配置：**
```python
PARAMS = {"top_n": 10, "rebalance_freq": 5, "max_weight": 0.10}
FACTOR_EXPRS = [{"factor_name": "momentum_20d", "factor_code": "close / ts_mean(close, 20) - 1", "weight": 1.0}]
```

#### 5. 参数优化（用户输入）
```
帮我测试不同的持仓数和调仓频率，找到最优参数
```

**LLM 执行流程：**
1. 创建Goal → "优化策略参数"
2. 迭代测试 top_n=[5,10,20] × freq=[5,10,20]
3. 每组参数执行 `run_backtest`
4. 收集结果 → `list_history`
5. 添加证据到Goal
6. 总结最优参数

#### 6. 报告生成（用户输入）
```
生成一份完整的因子研究报告，保存到策略目录
```

**LLM 执行流程：**
1. 读取历史回测结果
2. 汇总因子分析数据
3. `write_file("strategies/momentum_20d/report.md")` → 生成报告

---

### API 演示

#### Goal 系统
```bash
# 创建研究目标
curl -X POST http://127.0.0.1:8783/api/goal/start \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","objective":"分析20日动量因子IC"}'

# 查看目标状态
curl http://127.0.0.1:8783/api/goal/status?session_id=demo

# 添加证据
curl -X POST http://127.0.0.1:8783/api/goal/evidence \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","evidence":"IC均值=0.06, IR=0.5"}'

# 完成目标
curl -X POST http://127.0.0.1:8783/api/goal/complete \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","outcome":"complete"}'
```

#### 数据源
```bash
# 列出可用数据源
curl http://127.0.0.1:8783/api/chat/send_async  # (via tool call)

# 配置 auto+duckdb 缓存模式
data:
  source: auto+duckdb
  codes: [000001.SZ, 600519.SH]
```

---

### 核心功能展示

| 功能 | 工具/命令 | 说明 |
|------|----------|------|
| 数据获取 | `get_market_data` | 支持tencent/akshare等多源 |
| 数据导入 | `import_data` | 保存到DuckDB |
| 横截面IC | `factor_cross_sectional_analysis` | Pearson/Spearman IC |
| 五分位收益 | `factor_quintile_returns` | 分组回测 |
| IC衰减 | `factor_ic_decay` | 持有期分析 |
| 换手率 | `factor_turnover` | 成本估算 |
| 策略创建 | `write_file` + `run_backtest` | 配置+回测 |
| 目标管理 | `/api/goal/*` | 研究流程跟踪 |
| 缓存模式 | `auto+duckdb` | 自动缓存+刷新 |

---

### 数据流

```
用户输入
  ↓
LLM (AgentLoop)
  ↓
工具调用 (25个工具)
  ↓
┌─────────────────────────────────────┐
│  get_market_data (tencent)         │
│       ↓                             │
│  import_data (DuckDB)              │
│       ↓                             │
│  factor_cross_sectional_analysis   │
│       ↓                             │
│  run_backtest                      │
└─────────────────────────────────────┘
  ↓
SSE 事件流
  ↓
前端展示 (React)
```

---

### 文件结构

```
workspace/
├── data.duckdb              # 数据库
├── strategies/
│   └── momentum_20d/
│       ├── strategy.py      # 策略配置
│       ├── config.yaml      # 回测配置
│       ├── prepare.py       # 数据准备
│       └── runs/            # 回测结果
└── goals.db                 # 目标数据库
```

---

### Chat UI 特性

#### 工具调用展示
- **工具特定图标**: 30+ 工具映射专用图标（Code/FileText/Search/Play/Database/BarChart 等）
- **结果摘要**: 折叠时显示一行结果（如 "252 条行情数据", "Sharpe=0.82"）
- **智能摘要**: 根据工具类型自动选择摘要格式
- **状态动画**: 边框颜色 300ms 过渡动画
- **进度步骤**: 支持 tool_progress 事件显示工具内部步骤

#### 思考过程
- **Markdown 渲染**: 展开时渲染 markdown（支持代码高亮）
- **实时计时**: "Thinking for X.Xs" 动态更新
- **折叠摘要**: 默认折叠，显示时长

#### 流式文本
- **发光效果**: 流式输出时容器带 subtle glow
- **词边界对齐**: 不会切断单词
- **60fps 动画**: requestAnimationFrame 驱动

#### 错误处理
- **重试按钮**: 错误状态 hover 显示 RefreshCw 图标
- **错误高亮**: 红色边框 + 红色背景

---

### 前端开发模式（端口说明）

| 入口 | 地址 | 用途 |
|------|------|------|
| 部署轨 | `http://127.0.0.1:8783` | 后端托管构建版前端（`webui/static`），日常访问一律走这里 |
| 开发轨 | `http://localhost:5173` | Vite dev server，改前端代码热更新调试用 |

**开发模式启动：**
```bash
# 1. 启动后端（API 服务，8783）
cd /home/ll/Public/strategy-research
python -m agentnodes serve --host 127.0.0.1 --port 8783

# 2. 启动前端 dev server（5173，/api 代理到后端 8783）
cd webui/frontend
npm run dev
```

**说明：**
- 5173 是 Vite 默认端口，仅开发调试用；`/api` 请求由 vite proxy 转发到 `127.0.0.1:8783`
- 后端端口变更时无需改代码：`API_TARGET=http://127.0.0.1:9000 npm run dev`
- 构建产物输出到 `webui/static/`，由后端在 8783 统一托管（`vite.config.ts` → `build.outDir`）
