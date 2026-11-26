import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { FileJson, Pencil, Save, Loader2 } from 'lucide-react'
import type { DefinitionPayload } from '../../api/client'

interface ImportDefinitionDialogProps {
  open: boolean
  busy: boolean
  onImportToCanvas: (payload: DefinitionPayload) => void
  onSave: (payload: DefinitionPayload) => void
  onClose: () => void
}

export function ImportDefinitionDialog({
  open,
  busy,
  onImportToCanvas,
  onSave,
  onClose,
}: ImportDefinitionDialogProps) {
  const [text, setText] = useState('')
  const [error, setError] = useState('')

  const parse = (): DefinitionPayload | null => {
    setError('')
    try {
      const raw = JSON.parse(text)
      if (!raw || typeof raw !== 'object' || !Array.isArray(raw.nodes)) {
        setError('JSON 必须包含 nodes 数组')
        return null
      }
      return {
        name: String(raw.name ?? ''),
        description: raw.description ?? '',
        version: raw.version ?? '1.0',
        budget: raw.budget ?? {},
        llm: raw.llm ?? {},
        params: raw.params ?? {},
        nodes: raw.nodes,
        edges: raw.edges ?? [],
      }
    } catch (e) {
      setError(`JSON 解析失败：${(e as Error).message}`)
      return null
    }
  }

  const handleImport = () => {
    const payload = parse()
    if (payload) onImportToCanvas(payload)
  }

  const handleSave = () => {
    const payload = parse()
    if (payload) onSave(payload)
  }

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !v && !busy && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[520px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-strong bg-background p-5 shadow-2xl">
          <Dialog.Title className="flex items-center gap-2 text-sm font-medium text-foreground">
            <FileJson className="h-4 w-4 text-info-fg" /> 导入工作流定义 (JSON)
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-[11px] text-muted">
            粘贴 definition JSON（可来自导出的 workspace/workflows/*.json 或 git 版本化文件）。
          </Dialog.Description>

          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={12}
            spellCheck={false}
            placeholder='{
  "name": "my_workflow",
  "nodes": [
    {"id": "p", "type": "planner", "label": "生成计划", "config": {"max_steps": 6}},
    {"id": "e", "type": "evaluator", "label": "评估", "config": {}}
  ],
  "edges": [{"source": "p", "target": "e"}]
}'
            className="mt-3 w-full rounded border border-strong bg-slate-950 px-3 py-2 font-mono text-[11px] text-foreground outline-none focus:border-primary-500"
          />

          {error && (
            <div className="mt-2 rounded border border-rose-800 bg-rose-950/50 px-2 py-1 text-[11px] text-rose-300">
              {error}
            </div>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={onClose}
              disabled={busy}
              className="rounded px-3 py-1.5 text-xs text-muted hover:bg-surface-raised"
            >
              取消
            </button>
            <button
              onClick={handleImport}
              disabled={busy || !text.trim()}
              className="inline-flex items-center gap-1 rounded border border-strong bg-surface-raised px-3 py-1.5 text-xs text-foreground hover:bg-surface-raised disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Pencil className="h-3 w-3" />}
              导入到画布（不保存）
            </button>
            <button
              onClick={handleSave}
              disabled={busy || !text.trim()}
              className="inline-flex items-center gap-1 rounded bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              校验并保存
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
