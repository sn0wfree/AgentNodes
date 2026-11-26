import { useState } from 'react'
import { Table, ChevronDown, ChevronRight, ChevronLeft, Download } from 'lucide-react'
import type { TablePart } from '../../stores/chat'

interface TableBlockProps {
  table: TablePart
  maxVisibleRows?: number
}

export function TableBlock({ table, maxVisibleRows = 5 }: TableBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const [page, setPage] = useState(0)

  const { headers, rows, caption } = table
  const needsTruncation = rows.length > maxVisibleRows
  const visibleRows = needsTruncation && !expanded
    ? rows.slice(page * maxVisibleRows, (page + 1) * maxVisibleRows)
    : rows
  const totalPages = Math.ceil(rows.length / maxVisibleRows)

  const handleExportCSV = () => {
    const csv = [
      headers.join(','),
      ...rows.map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(',')),
    ].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${caption || 'table'}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="my-2 rounded-lg border border-subtle overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between bg-surface px-3 py-2">
        <div className="flex items-center gap-2">
          <Table className="h-3.5 w-3.5 text-muted" />
          <span className="text-xs text-secondary">
            {caption || '数据表格'}
          </span>
          <span className="text-[10px] text-muted">
            {rows.length} 行 × {headers.length} 列
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleExportCSV}
            className="rounded p-1 text-muted hover:text-secondary hover:bg-surface"
            title="导出 CSV"
          >
            <Download className="h-3 w-3" />
          </button>
          {needsTruncation && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-muted hover:text-secondary hover:bg-surface"
            >
              {expanded ? '收起' : `展开全部 (${rows.length})`}
              {expanded ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-surface-raised/30">
              {headers.map((h, i) => (
                <th
                  key={i}
                  className="px-3 py-2 text-left font-medium text-secondary border-b border-subtle whitespace-nowrap"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row, ri) => (
              <tr
                key={ri}
                className="hover:bg-surface-raised/30 transition-colors"
              >
                {row.map((cell, ci) => (
                  <td
                    key={ci}
                    className="px-3 py-1.5 border-b border-subtle text-secondary whitespace-nowrap"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination (when truncated) */}
      {needsTruncation && !expanded && totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-subtle px-3 py-1.5 text-[10px] text-muted">
          <span>
            第 {page + 1}/{totalPages} 页
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded p-0.5 hover:bg-surface disabled:opacity-30"
            >
              <ChevronLeft className="h-3 w-3" />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page === totalPages - 1}
              className="rounded p-0.5 hover:bg-surface disabled:opacity-30"
            >
              <ChevronRight className="h-3 w-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
