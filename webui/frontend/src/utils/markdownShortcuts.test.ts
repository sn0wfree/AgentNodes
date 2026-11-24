// R-014 F2/F3 —— Markdown 快捷操作纯函数（行首语义 + 包裹语义）。
import { describe, it, expect } from 'vitest'
import { wrapSelection, applyLinePrefix } from './markdownShortcuts'

describe('wrapSelection', () => {
  it('wraps the selection', () => {
    const r = wrapSelection('hello world', 6, 11, 'bold')
    expect(r.text).toBe('hello **world**')
    expect(r.selectionStart).toBe(r.selectionEnd)
  })

  it('inserts an empty marker pair when nothing is selected', () => {
    const r = wrapSelection('abc', 0, 0, 'bold')
    expect(r.text).toBe('****abc')
  })

  it('wraps a multi-line selection as one block', () => {
    const r = wrapSelection('x\ny', 0, 3, 'codeBlock')
    expect(r.text).toBe('```\nx\ny\n```')
  })

  it('inserts a link template when nothing is selected', () => {
    const r = wrapSelection('', 0, 0, 'link')
    expect(r.text).toBe('[文本]()')
  })
})

describe('applyLinePrefix · 无选区', () => {
  it('prefixes the caret line when the caret is at line start', () => {
    const r = applyLinePrefix('abc', 0, 0, 'ul')
    expect(r.text).toBe('- abc')
  })

  it('prefixes the whole line when the caret is mid-line (not mid-word)', () => {
    const r = applyLinePrefix('abc def', 4, 4, 'ul')
    expect(r.text).toBe('- abc def')
    expect(r.text).not.toContain('abc - def')
  })

  it('prefixes the caret line when the caret is at line end', () => {
    const r = applyLinePrefix('abc def', 7, 7, 'quote')
    expect(r.text).toBe('> abc def')
  })

  it('does not touch other lines', () => {
    const r = applyLinePrefix('one\ntwo\nthree', 5, 5, 'ul')
    expect(r.text).toBe('one\n- two\nthree')
  })
})

describe('applyLinePrefix · 多行选中', () => {
  it('prefixes every selected line', () => {
    const r = applyLinePrefix('a\nb\nc', 0, 5, 'ul')
    expect(r.text).toBe('- a\n- b\n- c')
  })

  it('numbers an ordered list sequentially', () => {
    const r = applyLinePrefix('a\nb\nc', 0, 5, 'ol')
    expect(r.text).toBe('1. a\n2. b\n3. c')
  })

  it('excludes the trailing line when the selection ends at its line start', () => {
    // 选中 "a\nb\n" —— 第三行未被选中（终点恰在其行首）
    const r = applyLinePrefix('a\nb\nc', 0, 4, 'ul')
    expect(r.text).toBe('- a\n- b\nc')
  })

  it('prefixes a single selected line', () => {
    const r = applyLinePrefix('a\nb\nc', 2, 3, 'quote')
    expect(r.text).toBe('a\n> b\nc')
  })
})

describe('applyLinePrefix · 选区位置', () => {
  it('keeps the selection over the same characters after prefixing', () => {
    const r = applyLinePrefix('abc def', 4, 7, 'ul')
    expect(r.text).toBe('- abc def')
    expect(r.text.slice(r.selectionStart, r.selectionEnd)).toBe('def')
  })

  it('places the caret after the prefix when nothing was selected', () => {
    const r = applyLinePrefix('abc', 0, 0, 'ul')
    expect(r.selectionStart).toBe(2)
    expect(r.text.slice(r.selectionStart)).toBe('abc')
  })
})
