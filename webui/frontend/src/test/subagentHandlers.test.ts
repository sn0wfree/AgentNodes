// hooks/sse/subagentHandlers — subagent lifecycle event handlers.
// Full lifecycle: started → tool_call → tool_result → text_delta →
// completed (and the failed path).

import { describe, it, expect, beforeEach } from 'vitest'
import {
  subagentStarted,
  subagentToolCall,
  subagentToolResult,
  subagentTextDelta,
  subagentCompleted,
  subagentFailed,
} from '../hooks/sse/subagentHandlers'
import { useChatStore } from '../stores/chat'
import type { SSEContext } from '../hooks/sse/types'
import type { AgentPart } from '../stores/chat'

function ctx(overrides: Partial<SSEContext> = {}): SSEContext {
  const store = useChatStore.getState()
  return {
    sessionId: 'sess-1',
    state: {
      hasSeenTotalTokens: () => false,
      getMessages: () => Array.from(store.messages.values()),
      getMessage: (id) => store.messages.get(id),
      isQueuePaused: () => false,
      getTokensUsed: () => 0,
    },
    addMessage: store.addMessage,
    updateMessage: store.updateMessage,
    applyTextDelta: store.applyTextDelta,
    setStreamingMessage: () => {},
    setStreamingText: () => {},
    appendStreamingText: () => {},
    setActiveAttempt: () => {},
    setQueuePaused: () => {},
    setQueueLength: () => {},
    setTokensUsed: () => {},
    markTotalTokensSeen: () => {},
    setLastCompaction: () => {},
    accumulatePartText: () => {},
    clearPartAccum: () => {},
    updateAgent: () => {},
    updateNodeStatus: () => {},
    setExecutionProgress: () => {},
    setGoal: () => {},
    updateGoal: () => {},
    addToast: () => {},
    addErrorToastOnce: (_key: string, _msg: string) => true,
    patchSessionMeta: () => {},
    ...overrides,
  } as SSEContext
}

function seedAssistant(msgId: string): void {
  useChatStore.setState((s) => {
    const m = new Map(s.messages)
    m.set(msgId, {
      id: msgId,
      session_id: 'sess-1',
      role: 'assistant',
      parts: [],
      created_at: 100,
    })
    return { messages: m }
  })
}

function agentParts(msgId: string): AgentPart[] {
  const m = useChatStore.getState().messages.get(msgId)
  return (m?.parts ?? []).filter((p): p is AgentPart => p.type === 'agent')
}

beforeEach(() => {
  useChatStore.setState({
    messages: new Map(),
    streamingMessageId: null,
    streamingText: '',
    partTextAccumDelta: {},
  })
})

describe('subagentStarted', () => {
  it('appends an AgentPart to the message', () => {
    seedAssistant('m1')
    subagentStarted(
      { agent_id: 'sub-1', name: 'explore', message_id: 'm1' },
      ctx(),
    )
    const parts = agentParts('m1')
    expect(parts).toHaveLength(1)
    const ap = parts[0]
    expect(ap.type).toBe('agent')
    expect(ap.agentId).toBe('sub-1')
    expect(ap.id).toBe('agent-sub-1')
    expect(ap.name).toBe('explore')
    expect(ap.status).toBe('running')
    expect(ap.toolCalls).toEqual([])
    expect(ap.streamingText).toBe('')
    expect(ap.isStreaming).toBe(true)
  })

  it('falls back to agent_id when name is missing', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-2', message_id: 'm1' }, ctx())
    expect(agentParts('m1')[0].name).toBe('sub-2')
  })

  it('does not error when the message is missing', () => {
    expect(() =>
      subagentStarted({ agent_id: 'sub-x', name: 'n', message_id: 'nope' }, ctx()),
    ).not.toThrow()
  })
})

describe('subagentToolCall', () => {
  it('appends a running tool call to the matching agent part', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'explore', message_id: 'm1' }, ctx())
    subagentToolCall(
      { agent_id: 'sub-1', tool_call_id: 'tc-1', name: 'read_file', arguments: '{"path":"x"}' },
      ctx(),
    )
    const ap = agentParts('m1')[0]
    expect(ap.toolCalls).toHaveLength(1)
    expect(ap.toolCalls[0]).toMatchObject({
      id: 'tc-1',
      name: 'read_file',
      status: 'running',
      isStreaming: true,
    })
  })

  it('ignores tool calls for unknown agents', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentToolCall(
      { agent_id: 'sub-other', tool_call_id: 'tc-9', name: 'x' },
      ctx(),
    )
    expect(agentParts('m1')[0].toolCalls).toHaveLength(0)
  })

  it('collects multiple tool calls in order', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentToolCall({ agent_id: 'sub-1', tool_call_id: 't1', name: 'read_file' }, ctx())
    subagentToolCall({ agent_id: 'sub-1', tool_call_id: 't2', name: 'write_file' }, ctx())
    const ap = agentParts('m1')[0]
    expect(ap.toolCalls.map((t) => t.id)).toEqual(['t1', 't2'])
  })
})

describe('subagentToolResult', () => {
  it('marks the matching tool call done with a result', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentToolCall({ agent_id: 'sub-1', tool_call_id: 'tc-1', name: 'read_file' }, ctx())
    subagentToolResult(
      { agent_id: 'sub-1', tool_call_id: 'tc-1', result: '{"rows": 10}', status: 'done' },
      ctx(),
    )
    const ap = agentParts('m1')[0]
    expect(ap.toolCalls[0].status).toBe('done')
    expect(ap.toolCalls[0].result).toBe('{"rows": 10}')
    expect(ap.toolCalls[0].isStreaming).toBe(false)
  })

  it('marks the matching tool call error', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentToolCall({ agent_id: 'sub-1', tool_call_id: 'tc-1', name: 'read_file' }, ctx())
    subagentToolResult(
      { agent_id: 'sub-1', tool_call_id: 'tc-1', result: '{"error":"boom"}', status: 'error' },
      ctx(),
    )
    expect(agentParts('m1')[0].toolCalls[0].status).toBe('error')
  })

  it('no-ops when the tool call id is unknown', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentToolCall({ agent_id: 'sub-1', tool_call_id: 'tc-1', name: 'read_file' }, ctx())
    subagentToolResult({ agent_id: 'sub-1', tool_call_id: 'nope', status: 'done' }, ctx())
    expect(agentParts('m1')[0].toolCalls[0].status).toBe('running')
  })
})

describe('subagentTextDelta', () => {
  it('accumulates streaming text on the agent part', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentTextDelta({ agent_id: 'sub-1', delta: 'hello' }, ctx())
    subagentTextDelta({ agent_id: 'sub-1', delta: ' world' }, ctx())
    expect(agentParts('m1')[0].streamingText).toBe('hello world')
  })

  it('ignores deltas for unknown agents', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentTextDelta({ agent_id: 'sub-other', delta: 'x' }, ctx())
    expect(agentParts('m1')[0].streamingText).toBe('')
  })
})

describe('subagentCompleted', () => {
  it('marks the agent part completed and stops streaming', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentCompleted({ agent_id: 'sub-1', tokens_used: 123 }, ctx())
    const ap = agentParts('m1')[0]
    expect(ap.status).toBe('completed')
    expect(ap.isStreaming).toBe(false)
    expect(ap.tokensUsed).toBe(123)
    expect(ap.finishedAt).toBeDefined()
  })

  it('leaves tokens_used undefined when not provided', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentCompleted({ agent_id: 'sub-1' }, ctx())
    expect(agentParts('m1')[0].tokensUsed).toBeUndefined()
  })
})

describe('subagentFailed', () => {
  it('marks the agent part failed with the error', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentFailed({ agent_id: 'sub-1', error: 'child boom' }, ctx())
    const ap = agentParts('m1')[0]
    expect(ap.status).toBe('failed')
    expect(ap.error).toBe('child boom')
    expect(ap.isStreaming).toBe(false)
    expect(ap.finishedAt).toBeDefined()
  })
})

describe('full lifecycle', () => {
  it('started → tool_call → tool_result → text_delta → completed', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'explore', message_id: 'm1' }, ctx())
    subagentToolCall({ agent_id: 'sub-1', tool_call_id: 'tc-1', name: 'list_files' }, ctx())
    subagentToolResult({ agent_id: 'sub-1', tool_call_id: 'tc-1', result: '{"n":3}', status: 'done' }, ctx())
    subagentTextDelta({ agent_id: 'sub-1', delta: 'found files' }, ctx())
    subagentCompleted({ agent_id: 'sub-1', tokens_used: 50 }, ctx())

    const ap = agentParts('m1')[0]
    expect(ap.status).toBe('completed')
    expect(ap.streamingText).toBe('found files')
    expect(ap.toolCalls).toHaveLength(1)
    expect(ap.toolCalls[0].status).toBe('done')
    expect(ap.tokensUsed).toBe(50)
  })

  it('handles multiple subagents in one message independently', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    subagentStarted({ agent_id: 'sub-2', name: 'b', message_id: 'm1' }, ctx())
    subagentTextDelta({ agent_id: 'sub-1', delta: 'A-text' }, ctx())
    subagentTextDelta({ agent_id: 'sub-2', delta: 'B-text' }, ctx())
    subagentCompleted({ agent_id: 'sub-1' }, ctx())
    subagentFailed({ agent_id: 'sub-2', error: 'boom' }, ctx())

    const parts = agentParts('m1')
    expect(parts).toHaveLength(2)
    const a = parts.find((p) => p.agentId === 'sub-1')!
    const b = parts.find((p) => p.agentId === 'sub-2')!
    expect(a.status).toBe('completed')
    expect(a.streamingText).toBe('A-text')
    expect(b.status).toBe('failed')
    expect(b.streamingText).toBe('B-text')
    expect(b.error).toBe('boom')
  })

  it('agent part ordering is preserved among other parts', () => {
    seedAssistant('m1')
    subagentStarted({ agent_id: 'sub-1', name: 'a', message_id: 'm1' }, ctx())
    // Simulate a text part arriving after the agent part
    useChatStore.getState().updateMessage('m1', (msg) => {
      msg.parts.push({ type: 'text', id: 't1', text: 'parent answer' })
    })
    const m = useChatStore.getState().messages.get('m1')!
    const types = m.parts.map((p) => p.type)
    expect(types).toEqual(['agent', 'text'])
  })
})
