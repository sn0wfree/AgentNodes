import { useState } from 'react'
import { FileEdit, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react'
import type { FileEditPart } from '../../stores/chat'
import { useCopyToClipboard } from '../../hooks/useCopyToClipboard'

interface FileEditBlockProps {
  fileEdit: FileEditPart
}

interface DiffLine {
  type: 'add' | 'remove' | 'context'
  lineNum: number
  content: string
}

function computeDiff(oldContent: string, newContent: string): DiffLine[] {
  const oldLines = oldContent.split('\n')
  const newLines = newContent.split('\n')
  const result: DiffLine[] = []

  // Simple line-by-line diff
  let oldIdx = 0
  let newIdx = 0

  while (oldIdx < oldLines.length || newIdx < newLines.length) {
    if (oldIdx >= oldLines.length) {
      // Remaining new lines are additions
      result.push({ type: 'add', lineNum: newIdx + 1, content: newLines[newIdx] })
      newIdx++
    } else if (newIdx >= newLines.length) {
      // Remaining old lines are removals
      result.push({ type: 'remove', lineNum: oldIdx + 1, content: oldLines[oldIdx] })
      oldIdx++
    } else if (oldLines[oldIdx] === newLines[newIdx]) {
      // Same line (context)
      result.push({ type: 'context', lineNum: newIdx + 1, content: newLines[newIdx] })
      oldIdx++
      newIdx++
    } else {
      // Different — show remove then add
      result.push({ type: 'remove', lineNum: oldIdx + 1, content: oldLines[oldIdx] })
      result.push({ type: 'add', lineNum: newIdx + 1, content: newLines[newIdx] })
      oldIdx++
      newIdx++
    }
  }

  return result
}

const LINE_STYLES = {
  add: 'bg-emerald-950/40 text-success-fg border-l-2 border-success-fg',
  remove: 'bg-red-950/40 text-danger-fg border-l-2 border-danger-fg',
  context: 'text-muted',
}

export function FileEditBlock({ fileEdit }: FileEditBlockProps) {
  const [expanded, setExpanded] = useState(true)
  const [copied, copy] = useCopyToClipboard()

  const diffLines = computeDiff(fileEdit.old_content, fileEdit.new_content)
  const added = diffLines.filter((l) => l.type === 'add').length
  const removed = diffLines.filter((l) => l.type === 'remove').length

  const allContent = diffLines
    .map((l) => {
      if (l.type === 'add') return `+ ${l.content}`
      if (l.type === 'remove') return `- ${l.content}`
      return `  ${l.content}`
    })
    .join('\n')

  const handleCopy = () => {
    copy(allContent)
  }

  return (
    <div className="my-2 rounded-lg border border-subtle overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 bg-surface px-3 py-2 text-left text-xs hover:bg-surface-raised/80 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 text-muted" />
        ) : (
          <ChevronRight className="h-3 w-3 text-muted" />
        )}
        <FileEdit className="h-3.5 w-3.5 text-primary-400" />
        <span className="font-mono text-secondary">{fileEdit.file_path}</span>
        <span className="ml-auto flex items-center gap-2 text-[10px]">
          <span className="text-success-fg">+{added}</span>
          <span className="text-danger-fg">-{removed}</span>
          <button
            onClick={(e) => { e.stopPropagation(); handleCopy() }}
            className="text-muted hover:text-secondary"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        </span>
      </button>

      {/* Diff content */}
      {expanded && (
        <div className="overflow-x-auto">
          <pre className="text-xs font-mono">
            {diffLines.map((line, i) => (
              <div key={i} className={`px-3 py-0.5 ${LINE_STYLES[line.type]}`}>
                <span className="inline-block w-8 text-right text-muted select-none mr-2">
                  {line.lineNum}
                </span>
                {line.content}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  )
}
