import { FolderOpen } from 'lucide-react'
import type { SystemInfo } from './types'
import { Section } from './shared'

export function WorkspaceSection({ systemInfo }: { systemInfo: SystemInfo | null }) {
  return (
    <Section icon={FolderOpen} title="工作区设置">
      {systemInfo ? (
        <div className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted">路径</span>
            <span className="text-foreground text-xs">{systemInfo.workspace_path}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">用户数</span>
            <span className="text-foreground">{systemInfo.user_count}</span>
          </div>
        </div>
      ) : (
        <p className="text-sm text-muted">加载中...</p>
      )}
    </Section>
  )
}