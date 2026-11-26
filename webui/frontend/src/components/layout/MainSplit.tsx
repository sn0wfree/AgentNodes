import { MessageList } from '../chat/MessageList'
import { Composer } from '../chat/Composer'
import { SessionTabs } from '../chat/SessionTabs'
import { TodoPanel } from '../chat/TodoPanel'

export function MainSplit() {
  return (
    <div className="relative flex flex-1 flex-col overflow-hidden bg-background">
      <SessionTabs />
      {/* Messages */}
      <div className="flex-1 overflow-hidden">
        <MessageList />
      </div>
      {/* Todo panel (opencode-style): floats above the composer, only
          mounts when todo_updated SSE pushes a non-empty list. */}
      <TodoPanel />
      {/* Composer */}
      <Composer />
    </div>
  )
}
