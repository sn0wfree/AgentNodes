/** TraceViewer — timeline view of agent trace events (DSH Trajectory View).

Displays LLM requests, responses, tool calls, and compaction events
as a scrollable timeline.  Fetches data from GET /api/chat/session/{id}/trace.
*/

import { useCallback, useEffect, useState } from "react";
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

interface TraceViewerProps {
  sessionId: string;
  onClose?: () => void;
}

const EVENT_COLORS: Record<string, string> = {
  llm_request: "border-blue-500/60 bg-blue-950/30",
  llm_response: "border-green-500/60 bg-green-950/30",
  tool_result: "border-amber-500/60 bg-amber-950/30",
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
        <div>
          <div className="mb-0.5 text-[9px] uppercase tracking-wide text-blue-400">
            System prompt ({event.system_prompt_len ?? systemPrompt.length} chars)
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-900/60 p-2 text-gray-300">
            {systemPrompt}
          </pre>
        </div>
      )}
      {toolsSchema && (
        <div>
          <div className="mb-0.5 text-[9px] uppercase tracking-wide text-violet-400">
            Tools schema ({event.tools_count ?? 0} tools)
          </div>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-900/60 p-2 text-gray-300">
            {tryJson(toolsSchema)}
          </pre>
        </div>
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
  const lines = diffLines(a, b);
  return (
    <div>
      <div className="mb-0.5 text-[9px] uppercase tracking-wide text-gray-400">{title}</div>
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
          <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-gray-400">
            {JSON.stringify(event, null, 2)}
          </pre>
        )
      )}
    </div>
  );
}

export function TraceViewer({ sessionId, onClose }: TraceViewerProps) {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [showDiff, setShowDiff] = useState(false);

  const fetchTrace = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({ limit: "200" });
      if (filter) params.set("types", filter);
      const data = await api.get<{ events: TraceEvent[] }>(
        `/chat/session/${encodeURIComponent(sessionId)}/trace?${params}`
      );
      setEvents(data.events ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load trace");
    } finally {
      setLoading(false);
    }
  }, [sessionId, filter]);

  useEffect(() => {
    fetchTrace();
  }, [fetchTrace]);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-gray-800 px-3 py-2">
        <span className="text-xs font-medium text-gray-300">Trace Timeline</span>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="ml-auto rounded border border-gray-700 bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-300"
        >
          <option value="">All events</option>
          <option value="llm_request,llm_response">LLM only</option>
          <option value="tool_result,tool_error">Tools only</option>
          <option value="loop_start,loop_end,iter_start">Loop lifecycle</option>
          <option value="error">Errors only</option>
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

      {/* Content */}
      <div className="flex-1 overflow-auto px-3 py-2">
        {loading && (
          <div className="py-4 text-center text-xs text-gray-500">Loading...</div>
        )}
        {error && (
          <div className="py-4 text-center text-xs text-red-400">{error}</div>
        )}
        {!loading && !error && events.length === 0 && (
          <div className="py-4 text-center text-xs text-gray-500">
            No trace events found
          </div>
        )}
        <TokenUsageChart events={events} />
        {showDiff && <LLMRequestDiff events={events} />}
        <div className="space-y-1">
          {events.map((event, i) => (
            <EventCard key={`${event.time_created ?? event.ts}-${i}`} event={event} />
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-800 px-3 py-1.5 text-center text-[9px] text-gray-600">
        {events.length} events · session {sessionId.slice(0, 8)}
      </div>
    </div>
  );
}
