/** TraceViewer — timeline view of agent trace events (DSH Trajectory View).

Displays LLM requests, responses, tool calls, and compaction events
as a scrollable timeline.  Fetches data from GET /api/chat/session/{id}/trace.
*/

import { useCallback, useEffect, useMemo, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../../api/client";

interface TraceEvent {
  ts?: number;
  /** event_log projection uses wall-clock time_created (seconds). */
  time_created?: number;
  type: string;
  iteration?: number;
  // LLM request envelope fields
  system_prompt?: string;
  system_prompt_len?: number;
  tools_schema?: string;
  tools_count?: number;
  history_meta?: unknown[];
  history_count?: number;
  [key: string]: unknown;
}

/** Pretty-print a JSON string (tools_schema); fall back to raw text. */
function tryJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

/**
 * Q4 (grilling): 敏感字段默认折叠 —— system_prompt / tools_schema
 * 可能含 API key 或用户数据，不再直接贴进 <pre>。显式按钮才展开，
 * 按钮文案带凭据警示；读屏 aria-expanded 同步。
 */
function SensitiveRaw({ label, value, meta }: { label: string; value: string; meta?: string }) {
  const [revealed, setRevealed] = useState(false);
  return (
    <div>
      <div className="mb-0.5 flex items-center gap-2 text-[9px] uppercase tracking-wide text-blue-400">
        <span>{label}</span>
        {meta && <span className="normal-case tracking-normal text-gray-500">{meta}</span>}
        <button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          aria-expanded={revealed}
          className="ml-auto rounded border border-gray-700 px-1 py-0.5 text-[9px] normal-case tracking-normal text-gray-400 hover:bg-gray-800"
        >
          {revealed ? "隐藏原始" : "显示原始（可能含凭据）"}
        </button>
      </div>
      {revealed ? (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-900/60 p-2 text-gray-300">{value}</pre>
      ) : (
        <div className="rounded bg-gray-900/40 p-2 text-[9px] italic text-gray-600">
          已折叠 · {value.length} 字符 · 点击上方按钮查看
        </div>
      )}
    </div>
  );
}

interface TraceViewerProps {
  sessionId: string;
  onClose?: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  llm_request: "border-blue-500/60 bg-blue-950/30",
  llm_response: "border-green-500/60 bg-green-950/30",
  tool_result: "border-violet-500/60 bg-violet-950/30",
  tool_error: "border-red-500/60 bg-red-950/30",
  loop_start: "border-purple-500/60 bg-purple-950/30",
  loop_end: "border-purple-500/60 bg-purple-950/30",
  iter_start: "border-slate-500/60 bg-slate-950/30",
  compression: "border-cyan-500/60 bg-cyan-950/30",
  error: "border-red-500/60 bg-red-950/30",
  heartbeat: "border-gray-500/40 bg-gray-950/20",
  tool_heartbeat: "border-gray-500/40 bg-gray-950/20",
};

const EVENT_ICONS: Record<string, string> = {
  llm_request: "▶",
  llm_response: "◀",
  tool_result: "⚙",
  tool_error: "✗",
  loop_start: "▶▶",
  loop_end: "■■",
  iter_start: "→",
  compression: "◆",
  error: "⚠",
  heartbeat: "♡",
  tool_heartbeat: "♡",
};

function formatTimestamp(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

/** Readable rendering of a reconstructed llm_request envelope. */
function LLMRequestDetails({ event }: { event: TraceEvent }) {
  const systemPrompt = typeof event.system_prompt === "string" ? event.system_prompt : "";
  const toolsSchema = typeof event.tools_schema === "string" ? event.tools_schema : "";
  const historyMeta: unknown[] = Array.isArray(event.history_meta)
    ? (event.history_meta as unknown[])
    : [];
  return (
    <div className="mt-1 space-y-2 font-mono text-[10px] leading-relaxed">
      {systemPrompt && (
        <SensitiveRaw
          label="System prompt"
          value={systemPrompt}
          meta={`${event.system_prompt_len ?? systemPrompt.length} chars`}
        />
      )}
      {toolsSchema && (
        <SensitiveRaw
          label="Tools schema"
          value={tryJson(toolsSchema)}
          meta={`${event.tools_count ?? 0} tools`}
        />
      )}
      {historyMeta.length > 0 && (
        <div className="text-gray-500">
          {historyMeta.length} history entries · {event.history_count ?? "?"} messages total
        </div>
      )}
    </div>
  );
}

/** Line-based LCS diff of two texts for the request-envelope comparison. */
function diffLines(a: string, b: string): { type: "same" | "add" | "del"; text: string }[] {
  const aa = a.split("\n");
  const bb = b.split("\n");
  const n = aa.length;
  const m = bb.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = aa[i] === bb[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: { type: "same" | "add" | "del"; text: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (aa[i] === bb[j]) {
      out.push({ type: "same", text: aa[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: "del", text: aa[i] });
      i++;
    } else {
      out.push({ type: "add", text: bb[j] });
      j++;
    }
  }
  while (i < n) out.push({ type: "del", text: aa[i++] });
  while (j < m) out.push({ type: "add", text: bb[j++] });
  return out;
}

interface DiffSectionProps {
  title: string;
  a: string;
  b: string;
}

/** A single section (system prompt / tools schema) of the envelope diff. */
function DiffSection({ title, a, b }: DiffSectionProps) {
  const [revealed, setRevealed] = useState(false); // Q4：diff 同样含原文，默认折叠
  const lines = diffLines(a, b);
  return (
    <div>
      <div className="mb-0.5 flex items-center gap-2 text-[9px] uppercase tracking-wide text-gray-400">
        <span>{title}</span>
        <button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          aria-expanded={revealed}
          className="ml-auto rounded border border-gray-700 px-1 py-0.5 text-[9px] normal-case tracking-normal text-gray-400 hover:bg-gray-800"
        >
          {revealed ? "隐藏 diff" : "显示 diff（可能含凭据）"}
        </button>
      </div>
      {!revealed ? (
        <div className="rounded bg-gray-900/40 p-2 text-[9px] italic text-gray-600">已折叠 · 点击上方按钮查看差异</div>
      ) : (
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-900/60 p-2 font-mono text-[10px] leading-relaxed">
        {lines.map((l, i) => (
          <div
            key={i}
            className={
              l.type === "add"
                ? "bg-green-900/40 text-green-300"
                : l.type === "del"
                  ? "bg-red-900/40 text-red-300"
                  : "text-gray-400"
            }
          >
            {l.type === "add" ? "+ " : l.type === "del" ? "- " : "  "}
            {l.text || " "}
          </div>
        ))}
      </pre>
      )}
    </div>
  );
}

/** Compare two llm_request envelopes (system prompt + tools schema). */
function LLMRequestDiff({ events }: { events: TraceEvent[] }) {
  const reqs = events.filter((e) => e.type === "llm_request");
  const [base, setBase] = useState(0);
  const [cmp, setCmp] = useState(reqs.length > 1 ? 1 : 0);
  const safeBase = Math.min(base, Math.max(0, reqs.length - 1));
  const safeCmp = Math.min(cmp, Math.max(0, reqs.length - 1));
  const a = reqs[safeBase];
  const b = reqs[safeCmp];

  if (reqs.length < 2) {
    return (
      <div className="px-1 pb-1 text-[10px] text-gray-500">
        Need at least two LLM requests to diff envelopes.
      </div>
    );
  }

  const text = (e: TraceEvent | undefined, key: string) =>
    typeof e?.[key] === "string" ? (e[key] as string) : "";

  return (
    <div className="border-b border-gray-800 px-3 py-2">
      <div className="mb-1 flex items-center gap-2 text-[10px]">
        <span className="text-gray-400">Envelope diff</span>
        <select
          value={safeBase}
          onChange={(e) => setBase(Number(e.target.value))}
          className="rounded border border-gray-700 bg-gray-800 px-1 py-0.5 text-gray-300"
        >
          {reqs.map((_, i) => (
            <option key={i} value={i}>
              base i{i + 1}
            </option>
          ))}
        </select>
        <span className="text-gray-600">vs</span>
        <select
          value={safeCmp}
          onChange={(e) => setCmp(Number(e.target.value))}
          className="rounded border border-gray-700 bg-gray-800 px-1 py-0.5 text-gray-300"
        >
          {reqs.map((_, i) => (
            <option key={i} value={i}>
              compare i{i + 1}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-2">
        <DiffSection title={`System prompt (${(a?.system_prompt_len ?? text(a, "system_prompt").length)} chars)`} a={text(a, "system_prompt")} b={text(b, "system_prompt")} />
        <DiffSection title="Tools schema" a={tryJson(text(a, "tools_schema"))} b={tryJson(text(b, "tools_schema"))} />
      </div>
    </div>
  );
}

/** Cumulative token-usage chart fed by session_total_tokens events. */
function TokenUsageChart({ events }: { events: TraceEvent[] }) {
  const series = events
    .filter((e) => e.type === "session_total_tokens")
    .map((e, i) => ({
      index: i + 1,
      total: typeof e.total_tokens === "number" ? e.total_tokens : 0,
    }));
  if (series.length < 2) return null;
  return (
    <div className="border-b border-gray-800 px-3 py-2">
      <div className="mb-1 text-[9px] uppercase tracking-wide text-gray-400">
        Cumulative tokens
      </div>
      <div className="h-24">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="tokenFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="index" tick={{ fontSize: 9, fill: "#6b7280" }} tickLine={false} axisLine={false} />
            <YAxis tick={{ fontSize: 9, fill: "#6b7280" }} tickLine={false} axisLine={false} width={40} />
            <Tooltip
              contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 10 }}
              labelFormatter={(l) => `LLM call #${l}`}
            />
            <Area type="monotone" dataKey="total" stroke="#3b82f6" fill="url(#tokenFill)" strokeWidth={1.5} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function EventCard({ event }: { event: TraceEvent }) {
  const [expanded, setExpanded] = useState(false);
  const [rawRevealed, setRawRevealed] = useState(false); // Q4：原始 JSON 默认折叠
  const color = EVENT_COLORS[event.type] ?? "border-gray-500/40 bg-gray-950/20";
  const icon = EVENT_ICONS[event.type] ?? "•";
  const ts = event.time_created ?? event.ts;

  const summary = (() => {
    switch (event.type) {
      case "llm_request":
        return `LLM Request — ${event.tools_count ?? 0} tools, ${event.history_count ?? 0} messages, ${event.system_prompt_len ?? 0} chars prompt`;
      case "llm_response":
        return `LLM Response — ${event.finish_reason ?? "?"}, ${event.tool_call_count ?? 0} tool calls`;
      case "tool_result":
        return `${event.tool ?? "?"} — ${event.status ?? "?"} (${event.elapsed_ms ?? 0}ms)`;
      case "tool_call":
        return `Tool Call — ${(event.name as string) ?? event.tool ?? "?"}`;
      case "tool_error":
        return `Tool Error — ${event.tool ?? "?"}`;
      case "loop_start":
        return `Loop Start — max ${event.max_iterations ?? "?"} iterations`;
      case "loop_end":
        return `Loop End — ${event.reason ?? "?"}, ${event.iteration ?? "?"} iters`;
      case "loop_final":
        return `Loop Final — ${event.reason ?? "?"}, ${event.iterations ?? "?"} iters, ${event.elapsed_s ?? "?"}s`;
      case "iter_start":
        return `Iteration ${event.iteration ?? "?"} — ~${event.tokens ?? "?"} tokens`;
      case "compression":
        return `Compression — ${event.applied ?? "?"}`;
      case "error":
        return `Error — ${event.error ?? "?"}`;
      case "heartbeat":
        return `Heartbeat — iteration ${event.iteration ?? "?"}`;
      case "tool_heartbeat":
        return `Heartbeat — ${event.tool ?? "?"} (${event.elapsed_s ?? "?"}s)`;
      default:
        return event.type;
    }
  })();

  return (
    <div className={`border-l-2 ${color} rounded-r-md px-3 py-1.5`}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 text-left text-xs"
      >
        <span className="flex-shrink-0 text-[10px] opacity-60">
          {icon}
        </span>
        <span className="flex-shrink-0 font-mono text-[9px] text-gray-500">
          {formatTimestamp(ts)}
        </span>
        {event.iteration != null && (
          <span className="flex-shrink-0 rounded bg-gray-800 px-1 py-0.5 font-mono text-[9px] text-gray-400">
            i{event.iteration}
          </span>
        )}
        <span className="truncate text-gray-300">{summary}</span>
        <span className="ml-auto flex-shrink-0 text-[10px] text-gray-600">
          {expanded ? "▾" : "▸"}
        </span>
      </button>
      {expanded && (
        event.type === "llm_request" ? (
          <LLMRequestDetails event={event} />
        ) : (
          <div className="mt-1">
            <button
              type="button"
              onClick={() => setRawRevealed((v) => !v)}
              aria-expanded={rawRevealed}
              className="rounded border border-gray-700 px-1 py-0.5 text-[9px] text-gray-400 hover:bg-gray-800"
            >
              {rawRevealed ? "隐藏原始" : "显示原始（可能含凭据）"}
            </button>
            {rawRevealed && (
              <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-gray-400">
                {JSON.stringify(event, null, 2)}
              </pre>
            )}
          </div>
        )
      )}
    </div>
  );
}

/** Q8：类型多选组（11 类归并为 5 个语义组；空集 = 不过滤）。 */
const TYPE_GROUPS = [
  { label: "LLM", types: ["llm_request", "llm_response"] },
  { label: "工具", types: ["tool_call", "tool_result", "tool_error", "tool_heartbeat"] },
  { label: "循环", types: ["loop_start", "loop_end", "loop_final", "iter_start"] },
  { label: "压缩", types: ["compression"] },
  { label: "错误", types: ["error"] },
];
const PAGE_SIZE = 50; // Q9：最近 50 条 + 加载更早

export function TraceViewer({ sessionId, onClose }: TraceViewerProps) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);
  const [iteration, setIteration] = useState<string>("");
  const [showDiff, setShowDiff] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const fetchTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "500" });
      if (selectedTypes.length) params.set("types", selectedTypes.join(","));
      const data = await api.get<{ events: TraceEvent[] }>(
        `/chat/session/${encodeURIComponent(sessionId)}/trace?${params}`
      );
      setEvents(data.events ?? []);
      setVisibleCount(PAGE_SIZE);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load trace");
    } finally {
      setLoading(false);
    }
  }, [sessionId, selectedTypes]);

  useEffect(() => {
    fetchTrace();
  }, [fetchTrace]);

  // Q8 迭代号下拉：从真实事件的 iteration 字段派生。
  const iterations = useMemo(
    () => Array.from(new Set(events.map((e) => e.iteration).filter((n): n is number => typeof n === "number"))).sort((a, b) => a - b),
    [events]
  );
  const filtered = useMemo(
    () => iteration ? events.filter((e) => String(e.iteration) === iteration) : events,
    [events, iteration]
  );
  const visible = filtered.slice(Math.max(0, filtered.length - visibleCount));

  return (
    <div className="flex h-full flex-col">
      {/* Header — Q8：类型多选 + 迭代号下拉（不做全文搜索） */}
      <div className="border-b border-gray-800 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-gray-300">Trace Timeline</span>
          <select
            value={iteration}
            onChange={(e) => { setIteration(e.target.value); setVisibleCount(PAGE_SIZE); }}
            aria-label="按迭代号筛选"
            className="ml-auto rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-300"
          >
            <option value="">全部迭代</option>
            {iterations.map((n) => (
              <option key={n} value={n}>i{n}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setShowDiff((v) => !v)}
            className={`rounded border px-1.5 py-0.5 text-[10px] ${
              showDiff
                ? "border-blue-500 bg-blue-900/40 text-blue-200"
                : "border-gray-700 text-gray-400 hover:bg-gray-800"
            }`}
          >
            Diff
          </button>
          <button
            type="button"
            onClick={fetchTrace}
            className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-800"
          >
            ↻
          </button>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-gray-700 px-1.5 py-0.5 text-[10px] text-gray-400 hover:bg-gray-800"
            >
              ✕
            </button>
          )}
        </div>
        {/* 类型多选 chips：空集=全部；点击切换，服务端 types 过滤 */}
        <div className="mt-1.5 flex flex-wrap gap-1" role="group" aria-label="事件类型筛选">
          {TYPE_GROUPS.map((g) => {
            const active = g.types.every((t) => selectedTypes.includes(t));
            return (
              <button
                key={g.label}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  setSelectedTypes((cur) => active
                    ? cur.filter((t) => !g.types.includes(t))
                    : Array.from(new Set([...cur, ...g.types])));
                }}
                className={`rounded px-1.5 py-0.5 text-[9px] ${
                  active ? "bg-blue-900/50 text-blue-200" : "bg-gray-800/60 text-gray-500 hover:text-gray-300"
                }`}
              >
                {g.label}
              </button>
            );
          })}
          {/* Q5：真实契约缺失但示意出现的字段——待接入灰字，不造数 */}
          <span className="ml-auto text-[9px] text-gray-600" title="真实 trace 契约无此字段">成本 / 模型名：待接入</span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-3 py-2">
        {loading && (
          <div className="py-4 text-center text-xs text-gray-500">Loading...</div>
        )}
        {error && (
          <div className="py-4 text-center text-xs text-red-400">{error}</div>
        )}
        {!loading && !error && filtered.length === 0 && (
          <div className="py-4 text-center text-xs text-gray-500">
            {events.length === 0 ? "No trace events found" : "当前筛选无事件"}
          </div>
        )}
        <TokenUsageChart events={filtered} />
        {showDiff && <LLMRequestDiff events={filtered} />}
        <div className="space-y-1">
          {visible.map((event, i) => (
            <EventCard key={`${event.time_created ?? event.ts}-${i}`} event={event} />
          ))}
        </div>
        {/* Q9：最近 50 条 + 加载更早 */}
        {filtered.length - visibleCount > 0 && (
          <button
            type="button"
            onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
            className="mt-2 w-full rounded border border-gray-700 py-1 text-[10px] text-gray-400 hover:bg-gray-800"
          >
            加载更早（剩余 {filtered.length - visibleCount} 条）
          </button>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-gray-800 px-3 py-1.5 text-center text-[9px] text-gray-600">
        {visible.length}/{filtered.length} events · session {sessionId.slice(0, 8)}
      </div>
    </div>
  );
}
