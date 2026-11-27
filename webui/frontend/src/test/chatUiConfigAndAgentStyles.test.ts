/**
 * Tests for chatUiConfig and agentStyles config modules.
 *
 * These modules load JSON config at import time. These tests verify
 * the accessor functions return the expected structure and values
 * from the config files.
 */
import { describe, it, expect } from 'vitest'

// ── agentStyles ──────────────────────────────────────────────

import {
  getAgentStyle,
  getCategoryStyle,
} from '../components/study/agentStyles'

describe('getAgentStyle', () => {
  it('returns a full AgentStyle for known agent ids', () => {
    const style = getAgentStyle('researcher')
    expect(style).toHaveProperty('name')
    expect(style).toHaveProperty('icon')
    expect(style).toHaveProperty('color')
    expect(style).toHaveProperty('category')
    // Derived Tailwind classes
    expect(style).toHaveProperty('text')
    expect(style).toHaveProperty('bg')
    expect(style).toHaveProperty('border')
    expect(style.text.startsWith('text-')).toBe(true)
    expect(style.bg.startsWith('bg-')).toBe(true)
  })

  it('falls back to default for unknown agent ids', () => {
    const style = getAgentStyle('nonexistent-agent-xyz')
    expect(style.name).toBeDefined()
    expect(style.icon).toBeDefined()
    expect(style.text.startsWith('text-')).toBe(true)
  })

  it('each color in COLOR_MAP produces valid Tailwind classes', () => {
    const agentIds = ['researcher', 'data_quality', 'factor_analyst',
      'strategist', 'portfolio_construction', 'risk_controller',
      'attribution_analyst', 'anti_overfit_analyst']
    for (const id of agentIds) {
      const style = getAgentStyle(id)
      expect(style.text).toMatch(/^text-\w+-\d+$/)
      expect(style.bg).toMatch(/^bg-\w+-500\/10$/)
      expect(style.border).toMatch(/^border-\w+-500\/30$/)
    }
  })
})

describe('getCategoryStyle', () => {
  it('returns category label and color', () => {
    const cat = getCategoryStyle('research')
    expect(cat).toHaveProperty('label')
    expect(cat).toHaveProperty('color')
  })
})

// ── chatUiConfig ─────────────────────────────────────────────

import {
  getChatConfig,
  getAssistantConfig,
  getUserBubbleConfig,
  getToolCallConfig,
  getThinkingConfig,
  getMessageListConfig,
  getPageShellConfig,
} from '../components/chat/chatUiConfig'

describe('getChatConfig', () => {
  it('returns the full config object', () => {
    const config = getChatConfig()
    expect(config).toHaveProperty('assistant')
    expect(config).toHaveProperty('userBubble')
    expect(config).toHaveProperty('toolCall')
    expect(config).toHaveProperty('thinking')
    expect(config).toHaveProperty('messageList')
    expect(config).toHaveProperty('pageShell')
  })

  it('assistant config has required sub-fields', () => {
    const a = getAssistantConfig()
    expect(a.avatar).toHaveProperty('icon')
    expect(a.avatar).toHaveProperty('size')
    expect(a.avatar).toHaveProperty('gradient')
    expect(a.labels).toHaveProperty('modelPrefix')
    expect(a.colors).toHaveProperty('streamingDot')
    expect(a.visibility).toHaveProperty('showVerifiabilityBadge')
  })

  it('toolCall config has dangerous tools list', () => {
    const tc = getToolCallConfig()
    expect(Array.isArray(tc.dangerousTools)).toBe(true)
    expect(tc.dangerousTools.length).toBeGreaterThan(0)
    expect(tc.labels).toHaveProperty('dangerousBadge')
  })

  it('thinking config has sizing constraints', () => {
    const th = getThinkingConfig()
    expect(th.sizing.maxHeight).toBeGreaterThan(0)
    expect(th.sizing.charThreshold).toBeGreaterThan(0)
  })

  it('messageList config has separator labels', () => {
    const ml = getMessageListConfig()
    expect(ml.labels.roundSeparator).toContain('{n}')
    expect(ml.labels.scrollToBottom).toBeTruthy()
  })

  it('pageShell config has layout dimensions', () => {
    const ps = getPageShellConfig()
    expect(ps.layout.headerHeight).toBeGreaterThan(0)
    expect(ps.layout.contentMaxWidth).toBeGreaterThan(0)
  })
})

describe('config round-trip correctness', () => {
  it('all get* functions return the same object identity as getChatConfig', () => {
    const full = getChatConfig()
    expect(getAssistantConfig()).toBe(full.assistant)
    expect(getUserBubbleConfig()).toBe(full.userBubble)
    expect(getToolCallConfig()).toBe(full.toolCall)
    expect(getThinkingConfig()).toBe(full.thinking)
    expect(getMessageListConfig()).toBe(full.messageList)
    expect(getPageShellConfig()).toBe(full.pageShell)
  })
})

describe('P0-3 config wiring contracts', () => {
  it('getMessageListConfig exposes labels consumed by MessageList', () => {
    const ml = getMessageListConfig()
    for (const key of ['loadMore', 'errorLabel', 'errorDetail', 'compactionLabel', 'scrollToBottom'] as const) {
      expect(typeof ml.labels[key]).toBe('string')
      expect(ml.labels[key].length).toBeGreaterThan(0)
    }
    for (const key of ['showQuickStartChips', 'showCompactBanner', 'showContextBar', 'showDaySeparators'] as const) {
      expect(typeof ml.visibility[key]).toBe('boolean')
    }
  })

  it('userBubble / thinking / toolCall / pageShell getters expose wired fields', () => {
    expect(getUserBubbleConfig().labels.userLabel).toBeTruthy()
    expect(getUserBubbleConfig().visibility.allowEdit).toBe(true)
    expect(getThinkingConfig().labels.streamingFormat).toContain('{duration}')
    expect(getToolCallConfig().dangerousTools.length).toBeGreaterThan(0)
    expect(getPageShellConfig().labels.themeLightLabel).toBeTruthy()
  })
})