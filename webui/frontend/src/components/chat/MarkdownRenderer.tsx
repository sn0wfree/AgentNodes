import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Copy, Check, Hash } from 'lucide-react'
import { memo, useCallback } from 'react'
import { useCopyToClipboard } from '../../hooks/useCopyToClipboard'

interface MarkdownRendererProps {
  content: string
  /** When true, skip Prism syntax highlighting (avoids per-char re-render cost). */
  streaming?: boolean
  /**
   * Opaque cache key (typically the content length for streaming
   * cases). When two renders share the same `cacheKey` AND the same
   * `streaming` flag, the component skips re-rendering entirely —
   * the opencode-style stable-prefix optimisation: a tail-only diff
   * (text appended at the end) with the same `content.length` for
   * the stable section can reuse the prior AST / DOM.
   */
  cacheKey?: string | number
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '-')
}

function CodeBlock({
  language,
  children,
  streaming,
}: {
  language: string
  children: string
  streaming?: boolean
}) {
  const [copied, copy] = useCopyToClipboard()

  const handleCopy = useCallback(() => {
    copy(children)
  }, [copy, children])

  return (
    <div className="group relative my-3 rounded-lg overflow-hidden border border-subtle">
      <div className="flex items-center justify-between bg-surface-raised/70 px-3 py-1 text-[11px] text-muted font-mono">
        <span>{language || 'text'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-muted hover:text-foreground opacity-0 group-hover:opacity-100 transition-opacity"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          <span>{copied ? '已复制' : '复制'}</span>
        </button>
      </div>
      {streaming ? (
        <pre className="m-0 overflow-x-auto bg-[var(--code-bg)] p-4 text-[0.8125rem] leading-relaxed font-mono text-foreground">
          {children}
        </pre>
      ) : (
        <SyntaxHighlighter
          language={language || 'text'}
          style={oneDark}
          customStyle={{
            margin: 0,
            borderRadius: 0,
            fontSize: '0.8125rem',
            lineHeight: '1.55',
            padding: '1rem',
          }}
        >
          {children}
        </SyntaxHighlighter>
      )}
    </div>
  )
}

function MarkdownRendererInner({ content, streaming }: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || '')
          const codeStr = String(children).replace(/\n$/, '')
          if (match) {
            return (
              <CodeBlock language={match[1]} streaming={streaming}>
                {codeStr}
              </CodeBlock>
            )
          }
          return (
            <code
              className="rounded-md bg-surface-raised/80 px-1.5 py-0.5 text-[0.85em] font-mono text-emerald-300 border border-strong/40"
              {...props}
            >
              {children}
            </code>
          )
        },
        table({ children }) {
          return (
            <div className="my-3 overflow-x-auto rounded-lg border border-subtle">
              <table className="w-full text-sm">{children}</table>
            </div>
          )
        },
        thead({ children }) {
          return <thead className="bg-surface-raised/70 sticky top-0">{children}</thead>
        },
        th({ children }) {
          return (
            <th className="px-3 py-2 text-left font-semibold text-foreground border-b border-subtle">
              {children}
            </th>
          )
        },
        td({ children }) {
          return (
            <td className="px-3 py-2 border-b border-subtle/40 text-secondary">
              {children}
            </td>
          )
        },
        tr({ children }) {
          return <tr className="hover:bg-surface-raised/30 transition-colors">{children}</tr>
        },
        p({ children }) {
          return <p className="my-3 leading-7 text-foreground">{children}</p>
        },
        ul({ children }) {
          return <ul className="my-2 list-disc pl-6 space-y-1.5 text-foreground marker:text-muted">{children}</ul>
        },
        ol({ children }) {
          return <ol className="my-2 list-decimal pl-6 space-y-1.5 text-foreground marker:text-muted">{children}</ol>
        },
        li({ children }) {
          return <li className="leading-7">{children}</li>
        },
        blockquote({ children }) {
          return (
            <blockquote className="my-3 border-l-4 border-primary-500/50 bg-surface-raised/30 pl-4 pr-3 py-2 text-secondary not-italic rounded-r">
              {children}
            </blockquote>
          )
        },
        h1({ children }) {
          const id = slugify(String(children))
          return (
            <h1
              id={id}
              className="group my-4 mt-6 text-2xl font-bold text-foreground scroll-mt-16 border-b border-subtle pb-2"
            >
              {children}
              <a href={`#${id}`} className="ml-2 opacity-0 group-hover:opacity-50 text-muted">
                <Hash className="inline h-4 w-4" />
              </a>
            </h1>
          )
        },
        h2({ children }) {
          const id = slugify(String(children))
          return (
            <h2
              id={id}
              className="group my-3 mt-5 text-xl font-semibold text-foreground scroll-mt-16"
            >
              {children}
              <a href={`#${id}`} className="ml-2 opacity-0 group-hover:opacity-50 text-muted">
                <Hash className="inline h-3.5 w-3.5" />
              </a>
            </h2>
          )
        },
        h3({ children }) {
          const id = slugify(String(children))
          return (
            <h3
              id={id}
              className="group my-2.5 mt-4 text-base font-semibold text-foreground scroll-mt-16"
            >
              {children}
              <a href={`#${id}`} className="ml-2 opacity-0 group-hover:opacity-50 text-muted">
                <Hash className="inline h-3 w-3" />
              </a>
            </h3>
          )
        },
        h4({ children }) {
          return <h4 className="my-2 mt-4 text-sm font-semibold text-secondary">{children}</h4>
        },
        strong({ children }) {
          return <strong className="font-semibold text-foreground">{children}</strong>
        },
        em({ children }) {
          return <em className="italic text-foreground">{children}</em>
        },
        a({ href, children }) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-400 underline decoration-primary-500/30 underline-offset-2 hover:decoration-primary-400"
            >
              {children}
            </a>
          )
        },
        hr() {
          return <hr className="my-5 border-subtle" />
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

/**
 * Memoised markdown renderer. Skips re-render when `cacheKey` and
 * `streaming` are unchanged (the stable-prefix case — a prior render
 * of the same content is reused verbatim). When `cacheKey` is omitted
 * we fall back to comparing `content` (less efficient but safe).
 */
export const MarkdownRenderer = memo(
  MarkdownRendererInner,
  (prev, next) => {
    if (prev.streaming !== next.streaming) return false
    if (prev.cacheKey !== undefined && next.cacheKey !== undefined) {
      return prev.cacheKey === next.cacheKey
    }
    return prev.content === next.content
  },
)