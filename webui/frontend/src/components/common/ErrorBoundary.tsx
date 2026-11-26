import { Component, type ReactNode } from 'react'
import { AlertCircle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <AlertCircle className="mb-4 h-12 w-12 text-danger-fg" />
          <h3 className="text-lg font-medium text-foreground">出错了</h3>
          <p className="mt-1 text-sm text-muted">
            {this.state.error?.message || '发生了未知错误'}
          </p>
          <button
            onClick={() => this.setState({ hasError: false })}
            className="mt-4 flex items-center gap-2 rounded-lg bg-surface-raised px-4 py-2 text-sm text-foreground hover:bg-slate-600"
          >
            <RefreshCw className="h-4 w-4" />
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
