// DefinitionWorkflowPage tests — Dify-style editor body:
// info bar (name dropdown/new/import/save/run), canvas, run drawer,
// goal history playback.

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  render,
  screen,
  waitFor as rtlWaitFor,
  fireEvent,
} from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { DefinitionWorkflowPage } from '../components/workflow/DefinitionWorkflowPage'
import { ApprovalDialog } from '../components/workflow/ApprovalDialog'
import type { DefinitionRunSnapshot } from '../api/client'

// Switch-the-definition flows chain two async loads (definitions.list →
// definitions.get) behind a fireEvent.change. Under coverage
// instrumentation the default 1000ms waitFor budget is occasionally
// exceeded — the suite flaked in CI runs with --coverage while passing
// without it. A 3s budget keeps the same assertions but removes the
// timing sensitivity.
const waitFor: typeof rtlWaitFor = ((cb: () => unknown, opts?: { timeout?: number }) =>
  rtlWaitFor(cb, { timeout: 3000, ...opts })) as typeof rtlWaitFor

vi.mock('../api/client', async () => {
  return {
    api: {
      definitions: {
        list: vi.fn(async () => ({
          status: 'ok',
          definitions: [
            { name: 'plan_execute_auto', source: 'builtin', description: '自动', node_count: 2 },
            { name: 'my_flow', source: 'user', description: '自定义', node_count: 1 },
          ],
        })),
        get: vi.fn(async (name: string) => ({
          status: 'ok',
          definition: { name, source: 'user', nodes: [], edges: [] },
        })),
        save: vi.fn(async () => ({ status: 'ok', name: 'x', path: '', nodes: 0, edges: 0 })),
        remove: vi.fn(async () => ({ status: 'ok', deleted: 'x' })),
        copy: vi.fn(async () => ({ status: 'ok', name: 'x', path: '' })),
        graph: vi.fn(async () => ({ status: 'ok', name: 'x', nodes: [], edges: [] })),
      },
      definitionRuns: {
        start: vi.fn(async () => ({
          status: 'ok',
          run_id: 'wf_test1',
          run: { run_id: 'wf_test1', definition: 'plan_execute_auto', status: 'completed',
                 completed_nodes: ['planner'], failures: [], findings: [],
                 segment_idx: 1, segments_total: 1, replan_count: 0, replan_max: 3,
                 elapsed_s: 1 } as DefinitionRunSnapshot,
        })),
        approve: vi.fn(async () => ({
          status: 'ok', run_id: 'wf_test1',
          run: { run_id: 'wf_test1', definition: 'plan_execute_auto', status: 'completed',
                 completed_nodes: ['planner', 'evaluator'], failures: [], findings: [],
                 segment_idx: 1, segments_total: 1, replan_count: 0, replan_max: 3, elapsed_s: 1 },
        })),
        status: vi.fn(async () => ({ status: 'ok', run_id: 'wf_test1', run: {} })),
        detail: vi.fn(async () => ({ status: 'ok', run: {}, segments: [], node_outputs: [], approvals: [] })),
        remove: vi.fn(async () => ({ status: 'ok', deleted: 'wf_test1' })),
      },
      // startEdit() restores the auto-saved orchestrator draft after the
      // definitions.get() load. Without this mock the call throws a
      // TypeError inside the (best-effort) restore branch, which in some
      // runs leaves the editor un-rendered and the 保存 assertion flaky.
      orchestrate: {
        getDraft: vi.fn(async () => ({ dag: null })),
        saveDraft: vi.fn(async () => ({ status: 'ok' })),
        clearDraft: vi.fn(async () => ({ status: 'ok', cleared: 'x' })),
        session: vi.fn(async () => ({ status: 'ok', session_id: 'sess-1' })),
      },
      goal: {
        list: vi.fn(async () => ({
          status: 'ok',
          goals: [
            { goal_id: 'g-hist', session_id: 'sess-1', goal_status: 'complete', objective: '历史目标',
              workflow_id: 'plan_execute_auto', created_at: '2026-08-01T10:00:00' },
          ],
        })),
      },
      workflow: {
        graph: vi.fn(async () => ({
          status: 'ok', name: 'plan_execute_auto',
          nodes: [{ id: 'planner', label: '生成计划' }], edges: [],
        })),
        status: vi.fn(async () => ({ status: 'not_found', goal_id: 'g-hist' })),
        pause: vi.fn(async () => ({ status: 'ok' })),
        resume: vi.fn(async () => ({ status: 'ok' })),
      },
    },
    ApiError: class extends Error {},
  }
})

vi.mock('lucide-react', () => {
  const Stub = () => null
  return {
    Activity: Stub,
    AlertCircle: Stub,
    AlertTriangle: Stub,
    Archive: Stub,
    ArrowLeft: Stub,
    ArrowRight: Stub,
    ArrowRightCircle: Stub,
    BarChart3: Stub,
    Bold: Stub,
    BookOpen: Stub,
    Bot: Stub,
    Brain: Stub,
    Calculator: Stub,
    CalendarCheck: Stub,
    ChartLine: Stub,
    Check: Stub,
    CheckCircle: Stub,
    CheckCircle2: Stub,
    ChevronDown: Stub,
    ChevronLeft: Stub,
    ChevronRight: Stub,
    ChevronUp: Stub,
    Circle: Stub,
    CircleDashed: Stub,
    ClipboardList: Stub,
    Clock: Stub,
    Code: Stub,
    Code2: Stub,
    Columns2: Stub,
    Command: Stub,
    Copy: Stub,
    Cpu: Stub,
    Database: Stub,
    Download: Stub,
    Edit3: Stub,
    ExternalLink: Stub,
    Eye: Stub,
    EyeOff: Stub,
    FileClock: Stub,
    FileCode2: Stub,
    FileEdit: Stub,
    FileJson: Stub,
    FileText: Stub,
    Folder: Stub,
    FolderOpen: Stub,
    Gauge: Stub,
    GitCompare: Stub,
    Globe: Stub,
    Hammer: Stub,
    Hash: Stub,
    HeartPulse: Stub,
    HelpCircle: Stub,
    History: Stub,
    Image: Stub,
    Inbox: Stub,
    Info: Stub,
    Italic: Stub,
    Layers: Stub,
    LayoutGrid: Stub,
    Library: Stub,
    LineChart: Stub,
    Link2: Stub,
    List: Stub,
    ListOrdered: Stub,
    Loader2: Stub,
    LogOut: Stub,
    MessageSquare: Stub,
    MessageSquareText: Stub,
    Minimize2: Stub,
    Minus: Stub,
    Moon: Stub,
    Network: Stub,
    Palette: Stub,
    PanelRight: Stub,
    PanelRightClose: Stub,
    Pause: Stub,
    PenTool: Stub,
    Pencil: Stub,
    Play: Stub,
    Plus: Stub,
    Quote: Stub,
    Redo2: Stub,
    RefreshCw: Stub,
    RotateCcw: Stub,
    Save: Stub,
    Search: Stub,
    Send: Stub,
    Settings: Stub,
    Shield: Stub,
    Sigma: Stub,
    SlidersHorizontal: Stub,
    Sparkles: Stub,
    Square: Stub,
    Star: Stub,
    Sun: Stub,
    Table: Stub,
    Tag: Stub,
    Target: Stub,
    ThumbsDown: Stub,
    ThumbsUp: Stub,
    Trash2: Stub,
    TrendingDown: Stub,
    TrendingUp: Stub,
    Undo2: Stub,
    User: Stub,
    Wifi: Stub,
    WifiOff: Stub,
    Workflow: Stub,
    Wrench: Stub,
    X: Stub,
    XCircle: Stub,
    Zap: Stub,
  }
})

vi.mock('@xyflow/react', async () => {
  const react = await import('react')
  return {
    ReactFlow: ({ children }: { children?: React.ReactNode }) =>
      react.createElement('div', { 'data-testid': 'reactflow' }, children),
    ReactFlowProvider: ({ children }: { children?: React.ReactNode }) =>
      react.createElement(react.Fragment, null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    BackgroundVariant: { Dots: 'dots' },
    addEdge: (conn: unknown, edges: unknown[]) => [...edges, conn],
    useReactFlow: () => ({
      screenToFlowPosition: (p: { x: number; y: number }) => p,
    }),
    useNodesState: (init: unknown[]) => {
      const [nodes, setNodes] = react.useState(init)
      return [nodes, setNodes, () => {}]
    },
    useEdgesState: (init: unknown[]) => {
      const [edges, setEdges] = react.useState(init)
      return [edges, setEdges, () => {}]
    },
  }
})

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => () => {} }
})

// jsdom lacks EventSource; OrchestratorChat opens a chat SSE connection on
// mount. Minimal stub: records the URL, never fires events, no timers.
class MockEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  readyState = MockEventSource.CONNECTING
  url: string
  onopen: ((ev: Event) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  private handlers: Record<string, Array<(ev: unknown) => void>> = {}
  constructor(url: string) {
    this.url = url
  }
  addEventListener(type: string, cb: (ev: unknown) => void) {
    ;(this.handlers[type] ??= []).push(cb)
  }
  close() {
    this.readyState = MockEventSource.CLOSED
  }
}
vi.stubGlobal('EventSource', MockEventSource)

import { api } from '../api/client'
import { useSessionStore } from '../stores/session'

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 'sess-1' })
  vi.mocked(api.definitions.list).mockClear()
  vi.mocked(api.definitions.get).mockClear()
  vi.mocked(api.definitions.save).mockClear()
  vi.mocked(api.definitionRuns.start).mockClear()
  vi.mocked(api.goal.list).mockClear()
  vi.mocked(api.workflow.graph).mockClear()
  vi.mocked(api.workflow.status).mockClear()
})

// Zustand stores are module-level singletons: RTL cleanup() unmounts the
// DOM but leaves store state behind, so a goal opened by the playback
// test can survive into the next test and suppress the editor. Reset the
// stores this page touches before each test.
beforeEach(() => {
  useSessionStore.setState({
    currentSessionId: 'sess-1',
    sessions: [],
    openSessionIds: [],
    searchOpen: false,
  } as never)
})

describe('DefinitionWorkflowPage (Dify-style info bar)', () => {
  it('shows empty state with the workflow dropdown listing definitions', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    const select = screen.getByTitle('切换工作流定义') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(['plan_execute_auto', 'my_flow']),
    )
  })

  it('switches workflow via the dropdown and enters edit mode', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    fireEvent.change(screen.getByTitle('切换工作流定义'), { target: { value: 'my_flow' } })
    await waitFor(() => expect(api.definitions.get).toHaveBeenCalledWith('my_flow'))
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    // empty canvas placeholder inside the editor
    expect(screen.getByText('空画布')).toBeTruthy()
  })

  it('saves via the info-bar 保存 button and stays in edit mode', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    fireEvent.change(screen.getByTitle('切换工作流定义'), { target: { value: 'my_flow' } })
    // 二级锚点：先确认 select 已切到目标定义（setEditingName 生效），
    // 再等编辑器信息栏出现——避免只盯链条末端的「保存」而在
    // coverage 插桩下踩到轮询边界。
    await waitFor(() =>
      expect((screen.getByTitle('切换工作流定义') as HTMLSelectElement).value).toBe('my_flow')
    )
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    fireEvent.click(screen.getByText('保存'))
    await waitFor(() => expect(api.definitions.save).toHaveBeenCalled())
    const payload = vi.mocked(api.definitions.save).mock.calls[0][0]
    expect(payload).toHaveProperty('name', 'my_flow')
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeTruthy())
  })

  it('creates a new definition with a name input, then saves', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    fireEvent.click(screen.getByText('新建'))
    await waitFor(() => expect(screen.getByPlaceholderText('定义名称')).toBeTruthy())
    expect(screen.getByText('空画布')).toBeTruthy()
    fireEvent.change(screen.getByPlaceholderText('定义名称'), { target: { value: 'brand_new' } })
    fireEvent.click(screen.getByText('保存'))
    await waitFor(() => expect(api.definitions.save).toHaveBeenCalled())
    expect(vi.mocked(api.definitions.save).mock.calls[0][0].name).toBe('brand_new')
  })

  it('adds a node from the palette and clears the empty canvas hint', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    fireEvent.change(screen.getByTitle('切换工作流定义'), { target: { value: 'my_flow' } })
    // 二级锚点：先确认 select 已切到目标定义（setEditingName 生效），
    // 再等编辑器信息栏出现——避免只盯链条末端的「保存」而在
    // coverage 插桩下踩到轮询边界。
    await waitFor(() =>
      expect((screen.getByTitle('切换工作流定义') as HTMLSelectElement).value).toBe('my_flow')
    )
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    expect(screen.getByText('空画布')).toBeTruthy()
    fireEvent.click(screen.getByText('生成计划'))
    await waitFor(() => expect(screen.queryByText('空画布')).not.toBeInTheDocument())
  })

  it('filters the palette by search', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    fireEvent.change(screen.getByTitle('切换工作流定义'), { target: { value: 'my_flow' } })
    // 二级锚点：先确认 select 已切到目标定义（setEditingName 生效），
    // 再等编辑器信息栏出现——避免只盯链条末端的「保存」而在
    // coverage 插桩下踩到轮询边界。
    await waitFor(() =>
      expect((screen.getByTitle('切换工作流定义') as HTMLSelectElement).value).toBe('my_flow')
    )
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText('搜索节点…'), { target: { value: '生成计划' } })
    expect(screen.getByText('生成计划')).toBeTruthy()
    expect(screen.queryByText('子 Agent')).not.toBeInTheDocument()
  })

  it('starts a run from the run drawer', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    fireEvent.change(screen.getByTitle('切换工作流定义'), { target: { value: 'plan_execute_auto' } })
    // 二级锚点：先确认 select 已切到目标定义（setEditingName 生效），
    // 再等编辑器信息栏出现——避免只盯链条末端的「保存」而在
    // coverage 插桩下踩到轮询边界。
    await waitFor(() =>
      expect((screen.getByTitle('切换工作流定义') as HTMLSelectElement).value).toBe('plan_execute_auto')
    )
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    fireEvent.click(screen.getByText('运行'))
    await waitFor(() => expect(screen.getByText('启动运行')).toBeTruthy())
    const objective = screen.getByPlaceholderText(/例：找出沪深300/)
    fireEvent.change(objective, { target: { value: '研究动量' } })
    fireEvent.click(screen.getByText('启动运行'))
    await waitFor(() => expect(api.definitionRuns.start).toHaveBeenCalled())
    const args = vi.mocked(api.definitionRuns.start).mock.calls[0]
    expect(args[0]).toBe('sess-1')
    expect(args[1]).toBe('plan_execute_auto')
    expect(args[2]).toBe('研究动量')
    await waitFor(() => expect(screen.getByText('wf_test1')).toBeTruthy())
  })
})

describe('Run history playback', () => {
  it('lists goals and opens a read-only playback view, then returns to editor', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    // enter edit mode first so the editor exists beneath playback
    fireEvent.change(screen.getByTitle('切换工作流定义'), { target: { value: 'my_flow' } })
    // 二级锚点：先确认 select 已切到目标定义（setEditingName 生效），
    // 再等编辑器信息栏出现——避免只盯链条末端的「保存」而在
    // coverage 插桩下踩到轮询边界。
    await waitFor(() =>
      expect((screen.getByTitle('切换工作流定义') as HTMLSelectElement).value).toBe('my_flow')
    )
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    fireEvent.click(screen.getByText('运行'))
    await waitFor(() => expect(screen.getByText('启动运行')).toBeTruthy())

    // expand run history and open the goal
    fireEvent.click(screen.getByText(/运行记录/))
    await waitFor(() => expect(screen.getByText('历史目标')).toBeTruthy())
    fireEvent.click(screen.getByText('历史目标'))
    await waitFor(() => expect(api.workflow.graph).toHaveBeenCalledWith('plan_execute_auto'))
    await waitFor(() => expect(api.workflow.status).toHaveBeenCalledWith('g-hist'))
    // playback header with 返回编辑
    await waitFor(() => expect(screen.getByText(/回看：plan_execute_auto/)).toBeTruthy())
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0)

    // back to the editor canvas
    fireEvent.click(screen.getByText('返回编辑'))
    await waitFor(() => expect(screen.getByText('空画布')).toBeTruthy())
  })
})

describe('ApprovalDialog', () => {
  it('renders plan preview and approve/reject actions', () => {
    const onApprove = vi.fn()
    const onReject = vi.fn()
    render(
      <ApprovalDialog
        open
        runId="wf_x"
        planPreview="计划步骤：plan_1 → plan_2"
        busy={false}
        onApprove={onApprove}
        onReject={onReject}
        onClose={() => {}}
      />,
    )
    expect(screen.getByText('工作流等待人工确认')).toBeTruthy()
    expect(screen.getByText(/plan_1 → plan_2/)).toBeTruthy()
    fireEvent.click(screen.getByText('批准执行'))
    expect(onApprove).toHaveBeenCalled()
    fireEvent.click(screen.getByText('拒绝并重规划'))
    expect(onReject).toHaveBeenCalled()
  })

  it('passes edit notes to approve/reject', () => {
    const onReject = vi.fn()
    render(
      <ApprovalDialog
        open
        runId="wf_x"
        planPreview=""
        busy={false}
        onApprove={() => {}}
        onReject={onReject}
        onClose={() => {}}
      />,
    )
    const notes = screen.getByPlaceholderText(/例：换一个方向/)
    fireEvent.change(notes, { target: { value: '先验证数据' } })
    fireEvent.click(screen.getByText('拒绝并重规划'))
    expect(onReject).toHaveBeenCalledWith('先验证数据')
  })
})

describe('JSON import', () => {
  const GOOD_JSON = JSON.stringify({
    name: 'imported_flow',
    nodes: [
      { id: 'p', type: 'planner', label: '生成计划', config: { max_steps: 6 } },
      { id: 'e', type: 'evaluator', label: '评估', config: {} },
    ],
    edges: [{ source: 'p', target: 'e' }],
  })

  const openImportDialog = async () => {
    fireEvent.click(screen.getByText('导入'))
    await waitFor(() => expect(screen.getByText('导入工作流定义 (JSON)')).toBeTruthy())
  }

  it('imports JSON into the canvas (edit without saving)', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    await openImportDialog()
    const ta = screen.getByPlaceholderText(/my_workflow/)
    fireEvent.change(ta, { target: { value: GOOD_JSON } })
    fireEvent.click(screen.getByText('导入到画布（不保存）'))
    await waitFor(() => expect(screen.getByText('保存')).toBeTruthy())
    // imported nodes loaded into the canvas: empty hint is gone
    expect(screen.queryByText('空画布')).not.toBeInTheDocument()
    expect(api.definitions.save).not.toHaveBeenCalled()
  })

  it('validates and saves JSON via API', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    await openImportDialog()
    const ta = screen.getByPlaceholderText(/my_workflow/)
    fireEvent.change(ta, { target: { value: GOOD_JSON } })
    fireEvent.click(screen.getByText('校验并保存'))
    await waitFor(() => expect(api.definitions.save).toHaveBeenCalled())
    const payload = vi.mocked(api.definitions.save).mock.calls[0][0]
    expect(payload.name).toBe('imported_flow')
    expect(payload.nodes).toHaveLength(2)
    expect(payload.edges).toEqual([{ source: 'p', target: 'e' }])
  })

  it('rejects invalid JSON with an error message', async () => {
    render(<MemoryRouter><DefinitionWorkflowPage /></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('选择或新建工作流')).toBeTruthy())
    await openImportDialog()
    const ta = screen.getByPlaceholderText(/my_workflow/)
    fireEvent.change(ta, { target: { value: '{not json' } })
    fireEvent.click(screen.getByText('校验并保存'))
    await waitFor(() => expect(screen.getByText(/JSON 解析失败/)).toBeTruthy())
    expect(api.definitions.save).not.toHaveBeenCalled()
  })
})
