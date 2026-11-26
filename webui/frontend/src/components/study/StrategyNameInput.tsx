import { useState, useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import {
  generateStrategyName,
  regenerateWithRandom,
  validateStrategyName,
  type StrategyNameParts,
} from '../../utils/strategyNameGenerator'

interface Props {
  objective: string
  userId: string
  sessionId: string
  value: string
  onChange: (value: string) => void
}

export function StrategyNameInput({
  objective,
  userId,
  sessionId,
  value,
  onChange,
}: Props) {
  const [parts, setParts] = useState<StrategyNameParts | null>(null)
  const [error, setError] = useState('')
  const [isManualEdit, setIsManualEdit] = useState(false)

  // Generate strategy name from objective
  const generateName = useCallback(() => {
    if (!objective.trim() || !userId || !sessionId) return

    const { name, parts: newParts } = generateStrategyName(
      objective,
      userId,
      sessionId
    )
    setParts(newParts)
    onChange(name)
    setIsManualEdit(false)
  }, [objective, userId, sessionId, onChange])

  // Auto-generate when objective changes (debounced)
  useEffect(() => {
    if (isManualEdit) return

    const timer = setTimeout(() => {
      generateName()
    }, 300)

    return () => clearTimeout(timer)
  }, [objective, userId, sessionId, generateName, isManualEdit])

  // Validate on change
  useEffect(() => {
    if (!value) {
      setError('')
      return
    }

    const { valid: _, error: err } = validateStrategyName(value)
    setError(err || '')
  }, [value])

  // Handle manual edit
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setIsManualEdit(true)
    onChange(e.target.value)
  }

  // Regenerate with new random
  const handleRegenerate = () => {
    if (!parts) return

    const { name } = regenerateWithRandom(parts)
    onChange(name)
    setIsManualEdit(true)
  }

  const charCount = value.length

  return (
    <div>
      <label className="block text-xs font-medium text-secondary mb-1">
        策略名称
      </label>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={handleChange}
          placeholder="输入研究目标后自动生成"
          className={`flex-1 rounded border px-2 py-1 text-sm font-mono ${
            error
              ? 'border-rose-500 bg-slate-900'
              : 'border-strong bg-slate-900'
          }`}
        />
        <button
          type="button"
          onClick={handleRegenerate}
          disabled={!parts}
          className="inline-flex items-center gap-1 rounded border border-strong bg-surface-raised px-2 py-1 text-xs text-muted hover:border-slate-500 hover:text-foreground disabled:opacity-50"
          title="重新生成"
        >
          <RefreshCw className="h-3 w-3" />
        </button>
      </div>
      <div className="flex items-center gap-2 mt-1">
        {error ? (
          <span className="text-[10px] text-danger-fg">{error}</span>
        ) : (
          <span className="text-[10px] text-muted">
            由研究目标自动生成 · 可手动修改 · {charCount} 字符
          </span>
        )}
      </div>
    </div>
  )
}
