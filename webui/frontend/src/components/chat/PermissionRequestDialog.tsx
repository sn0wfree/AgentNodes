import { useState } from 'react'
import { Shield, X } from 'lucide-react'
import type { PermissionRequest } from '../../hooks/sse/permissionHandlers'

interface PermissionRequestDialogProps {
  request: PermissionRequest | null
  onRespond: (action: 'allow' | 'deny', permanent: boolean, reason?: string) => void
  submitting?: boolean
  error?: string
}

const ACTION_LABELS = {
  write_file: '写入文件',
  edit: '编辑文件',
  delete_file: '删除文件',
  read_file: '读取文件',
  list_files: '列出文件',
  list_history: '查看历史',
  run_command: '运行命令',
  run_backtest: '运行回测',
  get_market_data: '获取行情数据',
  import_data: '导入数据',
  compute_factor: '计算因子',
  web_fetch: '抓取网页',
  web_search: '搜索网页',
  delegate_to_agent: '委派子任务',
} as Record<string, string>

/**
 * Modal shown when the backend emits a `permission_request` SSE event.
 *
 * Mirrors opencode's `Permission.ask` UX: the user can approve the
 * current call (once), approve all future calls matching the rule
 * pattern (always), reject once, or reject always. Permanent choices
 * persist a rule to `~/.quantnodes-research/permissions.yaml` so the
 * evaluator short-circuits on subsequent calls.
 */
export function PermissionRequestDialog({
  request,
  onRespond,
  submitting = false,
  error,
}: PermissionRequestDialogProps) {
  const [reason, setReason] = useState('')

  if (!request) return null

  const label = ACTION_LABELS[request.tool_name] ?? request.tool_name
  const deny = (permanent: boolean) => {
    if (reason) onRespond('deny', permanent, reason)
    else onRespond('deny', permanent)
  }

  // Render the args in a copy-paste-friendly block. Keep tool
  // metadata out so the user sees the actual intent ("path: x" vs
  // the whole internal kwargs dict).
  const argEntries = Object.entries(request.args).filter(
    ([k]) => !k.startsWith('__') && !k.endsWith('__'),
  )

  return (
    <div
      data-testid="permission-request-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="permission-title"
      aria-busy={submitting}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
    >
      <div className="w-full max-w-lg rounded-xl border border-warning/60 bg-background shadow-2xl">
        {/* Header */}
        <div className="flex items-start gap-3 border-b border-warning/40 px-5 py-4">
          <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-warning-fill text-warning-fg">
            <Shield className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2
              id="permission-title"
              className="text-sm font-semibold text-foreground"
            >
              允许{label}吗？
            </h2>
            <p className="mt-0.5 text-[11px] text-muted">
              工具:{' '}
              <span className="font-mono text-warning-fg">{request.tool_name}</span>
              {' · 规则匹配: '}
              <span className="font-mono text-secondary">
                {request.pattern || '*'}
              </span>
            </p>
          </div>
          <button
            type="button"
            onClick={() => deny(false)}
            disabled={submitting}
            className="rounded p-1 text-muted hover:bg-surface-raised hover:text-foreground"
            title="关闭（拒绝）"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Args preview */}
        <div className="max-h-48 overflow-y-auto px-5 py-3">
          {argEntries.length === 0 ? (
            <p className="text-[11px] text-muted">（无可显示参数）</p>
          ) : (
            <table className="w-full text-[11px]">
              <tbody>
                {argEntries.map(([k, v]) => (
                  <tr key={k} className="border-b border-subtle/60 last:border-0">
                    <td className="py-1 pr-3 align-top font-mono text-muted">{k}</td>
                    <td className="py-1 font-mono text-foreground break-all">
                      {formatValue(v)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Optional reason (for reject) */}
        <div className="border-t border-subtle px-5 py-3">
          <label className="text-[11px] text-muted">
            拒绝原因（可选）
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              placeholder="例如：会覆盖现有策略文件"
              className="mt-1 w-full rounded border border-strong bg-surface-raised px-2 py-1.5 text-xs text-foreground placeholder-slate-500 outline-none focus:border-warning"
              data-testid="permission-reason-input"
            />
          </label>
        </div>

        {error && <p role="alert" className="px-5 pb-2 text-xs text-danger-fg">{error}</p>}

        {/* Action buttons (opencode's 4-way gate) */}
        <div className="grid grid-cols-2 gap-2 border-t border-subtle p-3">
          <button
            type="button"
            onClick={() => onRespond('allow', false)}
            disabled={submitting}
            data-testid="permission-allow-once"
            className="rounded-md border border-emerald-700/50 bg-emerald-900/20 px-3 py-2 text-xs text-emerald-200 transition-colors hover:bg-emerald-900/40"
          >
            允许本次
          </button>
          <button
            type="button"
            onClick={() => onRespond('allow', true)}
            disabled={submitting}
            data-testid="permission-allow-always"
            className="rounded-md border border-emerald-700/60 bg-emerald-700/30 px-3 py-2 text-xs text-emerald-100 transition-colors hover:bg-emerald-700/50"
          >
            始终允许
          </button>
          <button
            type="button"
            onClick={() => deny(false)}
            disabled={submitting}
            data-testid="permission-deny-once"
            className="rounded-md border border-red-700/50 bg-red-900/20 px-3 py-2 text-xs text-red-200 transition-colors hover:bg-red-900/40"
          >
            拒绝本次
          </button>
          <button
            type="button"
            onClick={() => deny(true)}
            disabled={submitting}
            data-testid="permission-deny-always"
            className="rounded-md border border-red-700/60 bg-red-700/30 px-3 py-2 text-xs text-red-100 transition-colors hover:bg-red-700/50"
          >
            始终拒绝
          </button>
        </div>
      </div>
    </div>
  )
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return JSON.stringify(v, null, 0)
  } catch {
    return String(v)
  }
}