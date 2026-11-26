import {
  Bot,
  CalendarCheck,
  ClipboardList,
  Gauge,
  Code2,
  Wrench,
} from 'lucide-react'

// ── Node palette (mirrors backend NODE_METADATA) ───────────────
// Shared by WorkflowEditor (palette/config UI) and dagSpec
// (LLM-output validation/sanitization). Kept in its own module so
// dagSpec does not import the editor (avoid import cycles).

export const NODE_PALETTE = [
  {
    type: 'llm_agent',
    label: '子 Agent',
    icon: Bot,
    color: 'var(--wf-node-1)',
    desc: '完整 chat 子 agent（角色+提示词+工具）',
    defaults: { role: 'researcher' },
  },
  {
    type: 'planner',
    label: '生成计划',
    icon: ClipboardList,
    color: 'var(--wf-node-2)',
    desc: '目标 → 3-8 步研究子图',
    defaults: { max_steps: 6 },
  },
  {
    type: 'evaluator',
    label: '评估进度',
    icon: Gauge,
    color: 'var(--wf-node-3)',
    desc: 'continue / replan / stop 决策',
    defaults: {},
  },
  {
    type: 'approval',
    label: '人工确认',
    icon: CalendarCheck,
    color: 'var(--wf-node-4)',
    desc: '暂停等待用户审批（图切点）',
    defaults: {},
  },
  {
    type: 'python',
    label: 'Python 函数',
    icon: Code2,
    color: 'var(--wf-node-5)',
    desc: '调用注册的 Python 函数',
    defaults: { function: '' },
  },
  {
    type: 'tool',
    label: '调用工具',
    icon: Wrench,
    color: 'var(--wf-node-6)',
    desc: '直接调用注册工具（run_backtest 等）',
    defaults: { tool: 'run_backtest' },
  },
] as const

export type PaletteItem = (typeof NODE_PALETTE)[number]

export const TYPE_META = Object.fromEntries(
  NODE_PALETTE.map((p) => [p.type, p]),
) as Record<string, PaletteItem>

export const CONFIG_FIELDS: Record<string, Array<{ key: string; label: string; type: 'text' | 'select' | 'number'; options?: string[]; placeholder?: string }>> = {
  llm_agent: [
    { key: 'role', label: '角色', type: 'select',
      options: ['researcher', 'data_quality', 'factor_analyst', 'strategist', 'backtest_diagnostics', 'critic'] },
    { key: 'prompt_text', label: '附加指令', type: 'text', placeholder: '节点专属任务指令' },
    { key: 'max_iterations', label: '迭代上限', type: 'number' },
  ],
  planner: [
    { key: 'max_steps', label: '计划步数 (3-8)', type: 'number' },
  ],
  evaluator: [],
  approval: [
    { key: 'timeout', label: '超时秒 (空=永久等待)', type: 'number' },
  ],
  python: [
    { key: 'function', label: '函数名', type: 'text', placeholder: '已注册的 Python 函数' },
  ],
  tool: [
    { key: 'tool', label: '工具名', type: 'select',
      options: ['run_backtest', 'get_market_data', 'check_data', 'clean_data', 'compute_factor', 'factor_analysis', 'search_symbol'] },
  ],
}
