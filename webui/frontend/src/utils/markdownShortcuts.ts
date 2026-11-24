/**
 * Markdown 快捷操作 —— 纯文本变换（R-014 F2/F3 · composer-toolbar-p0p1-fix）。
 *
 * 为什么独立成模块：这些变换是**纯函数**（文本 → 文本 + 光标位置），
 * 放在组件里就只能靠 DOM 交互来测；抽出后可直接单测边界（行首 / 行中 /
 * 多行 / 无选区），也便于 Composer 与快捷键共用同一套语义。
 *
 * 两类语义（业界编辑器惯例）：
 * - **包裹型**（wrap）：粗体 / 斜体 / 行内代码 / 代码块 / 链接——套在
 *   选区两侧，选区为空时插入空标记。
 * - **行前缀型**（line-prefix）：无序列表 / 有序列表 / 引用——必须落在
 *   **整行行首**；光标在行中时给整行加前缀，多行选中时每行都加。
 */

/** 包裹型操作的语义标签。 */
export type WrapKind = 'bold' | 'italic' | 'code' | 'codeBlock' | 'link'

/** 行前缀型操作的语义标签。 */
export type LinePrefixKind = 'ul' | 'ol' | 'quote'

/** 变换结果：新文本 + 新的选区（caret 位置）。 */
export interface TextTransform {
  text: string
  selectionStart: number
  selectionEnd: number
}

const WRAP_SPECS: Record<WrapKind, { prefix: string; suffix: string }> = {
  bold: { prefix: '**', suffix: '**' },
  italic: { prefix: '_', suffix: '_' },
  code: { prefix: '`', suffix: '`' },
  codeBlock: { prefix: '```\n', suffix: '\n```' },
  link: { prefix: '[文本](', suffix: ')' },
}

/** 行前缀型：前缀文本；有序列表按行序递增编号，故为函数。 */
const LINE_PREFIX_SPECS: Record<LinePrefixKind, (ordinal: number) => string> = {
  ul: () => '- ',
  ol: (ordinal) => `${ordinal + 1}. `,
  quote: () => '> ',
}

/** 把 [start, end) 选区包在 prefix/suffix 之间。 */
export function wrapSelection(
  text: string,
  start: number,
  end: number,
  kind: WrapKind,
): TextTransform {
  const { prefix, suffix } = WRAP_SPECS[kind]
  const sel = text.slice(start, end)
  const before = text.slice(0, start)
  const after = text.slice(end)
  const wrapped = sel ? `${prefix}${sel}${suffix}` : `${prefix}${suffix}`
  const next = `${before}${wrapped}${after}`
  const caret = before.length + prefix.length + sel.length
  return { text: next, selectionStart: caret, selectionEnd: caret }
}

/** 计算某位置所在行的起始偏移。 */
function lineStartAt(text: string, pos: number): number {
  const idx = text.lastIndexOf('\n', Math.max(0, pos - 1))
  return idx === -1 ? 0 : idx + 1
}

/**
 * 给行前缀型操作加前缀：
 * - 无选区 → 只给光标所在行加；
 * - 选区在同一行 → 给该行加；
 * - 选区跨多行 → 给每个涉及的行都加（有序列表按行序递增编号）。
 *
 * 前缀一律插到**行首**，因此光标在行中时不会把 `- ` 插进词中间。
 */
export function applyLinePrefix(
  text: string,
  start: number,
  end: number,
  kind: LinePrefixKind,
): TextTransform {
  const prefixFor = LINE_PREFIX_SPECS[kind]
  const firstLineStart = lineStartAt(text, start)
  const lastLineStart = lineStartAt(text, end)

  // 受影响的行区间：从光标所在行到选区结束所在行。
  // 选区终点恰好落在某行行首时，那一行不算被选中。
  const firstLine = text.slice(0, firstLineStart).split('\n').length - 1
  let lastLine = text.slice(0, lastLineStart).split('\n').length - 1
  if (end > start && lastLineStart === end && lastLine > firstLine) {
    lastLine -= 1
  }

  const lines = text.split('\n')
  let shift = 0 // 光标之前插入的字符数
  let ordinal = 0
  for (let i = 0; i < lines.length; i++) {
    const inRange = i >= firstLine && i <= lastLine
    const prefix = inRange ? prefixFor(ordinal) : ''
    if (inRange) ordinal += 1
    if (i < firstLine || i === firstLine) {
      shift += prefix.length
    }
    lines[i] = prefix + lines[i]
  }

  const next = lines.join('\n')
  return {
    text: next,
    selectionStart: start + shift,
    selectionEnd: end + shift,
  }
}
