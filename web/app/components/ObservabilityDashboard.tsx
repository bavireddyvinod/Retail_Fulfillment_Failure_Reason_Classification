"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Activity, ArrowLeft, ArrowDownToLine, ArrowUpRight, CircleAlert, Clock3, Database, FileSearch, GitBranch, ShieldCheck, Siren } from "lucide-react";

export type ObservabilityView = "overview" | "logs" | "metrics" | "traces" | "drift" | "guardrails" | "approvals" | "trace-detail";
type DataRecord = Record<string, unknown>;
type DashboardPayload = DataRecord & { items?: DataRecord[]; requests?: DataRecord; llm?: DataRecord; agents?: DataRecord; tools?: DataRecord; rag?: DataRecord; infrastructure?: DataRecord; database?: DataRecord; pending?: DataRecord[]; decisions?: DataRecord[]; spans?: DataRecord[]; snapshot?: DataRecord; total?: number; blocked?: number };

const API_URL = "/api/backend";
const routes: { key: ObservabilityView; label: string; href: string }[] = [
  { key: "overview", label: "Overview", href: "/observability" },
  { key: "logs", label: "Logs", href: "/logs" },
  { key: "metrics", label: "Metrics", href: "/metrics" },
  { key: "traces", label: "Traces", href: "/traces" },
  { key: "drift", label: "Drift", href: "/drift" },
  { key: "guardrails", label: "Guardrails", href: "/guardrails" },
  { key: "approvals", label: "Approvals", href: "/approvals" },
];

function text(value: unknown, fallback = "—") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function number(value: unknown, suffix = "") {
  return typeof value === "number" ? `${Number.isInteger(value) ? value : value.toFixed(2)}${suffix}` : "Unavailable";
}

function eventRows(items: DataRecord[], traceLinks = false) {
  return <div className="obs-table-scroll"><table className="obs-table"><thead><tr><th>Timestamp</th><th>Level</th><th>Event</th><th>Agent / Tool</th><th>Status</th><th>Latency</th><th>Correlation</th></tr></thead><tbody>{items.map((item, index) => <tr key={text(item.id, `${index}`)}><td className="obs-mono">{text(item.timestamp)}</td><td><span className={`obs-level level-${text(item.severity, "info").toLowerCase()}`}>{text(item.severity)}</span></td><td>{text(item.event_type)}</td><td>{text(item.agent_name ?? item.tool_name ?? item.model_name)}</td><td>{text(item.status)}</td><td>{number(item.latency_ms, " ms")}</td><td>{traceLinks && item.trace_id ? <Link className="obs-trace-link" href={`/traces/${text(item.trace_id)}`}>{text(item.trace_id).slice(0, 8)} <ArrowUpRight size={12} /></Link> : <span className="obs-mono">{text(item.trace_id, "—").slice(0, 12)}</span>}</td></tr>)}</tbody></table></div>;
}

function downloadEvents(items: DataRecord[], format: "json" | "csv") {
  const fields = ["timestamp", "severity", "event_type", "service", "agent_name", "tool_name", "status", "latency_ms", "request_id", "trace_id", "workflow_id"];
  const escape = (value: unknown) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const body = format === "json" ? JSON.stringify(items, null, 2) : [fields.join(","), ...items.map((item) => fields.map((field) => escape(item[field])).join(","))].join("\n");
  const blob = new Blob([body], { type: format === "json" ? "application/json" : "text/csv" });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `observability-events.${format}`;
  anchor.click();
  URL.revokeObjectURL(href);
}

export default function ObservabilityDashboard({ view, traceId }: { view: ObservabilityView; traceId?: string }) {
  const [payload, setPayload] = useState<DashboardPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [range, setRange] = useState("1440");
  const [page, setPage] = useState(1);
  const [dataOrigin, setDataOrigin] = useState("LIVE");
  const [severity, setSeverity] = useState("");
  const [eventType, setEventType] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      let path = `/api/observability/overview?data_origin=${dataOrigin}`;
      if (view === "logs") {
        const params = new URLSearchParams({ page: String(page), page_size: "30", since_minutes: range, data_origin: dataOrigin });
        if (query) params.set("search", query);
        if (severity) params.set("severity", severity);
        if (eventType) params.set("event_type", eventType);
        path = `/api/logs?${params}`;
      }
      if (view === "metrics") path = `/api/metrics/requests?since_minutes=${range}&data_origin=${dataOrigin}`;
      if (view === "traces") path = `/api/traces?page=${page}&page_size=30&data_origin=${dataOrigin}`;
      if (view === "trace-detail") path = `/api/traces/${encodeURIComponent(traceId ?? "")}`;
      if (view === "drift") path = `/api/drift?data_origin=${dataOrigin}`;
      if (view === "guardrails") path = `/api/guardrails?page=${page}&page_size=30&data_origin=${dataOrigin}`;
      if (view === "approvals") path = `/api/approvals?page=${page}&page_size=30`;
      try {
        const response = await fetch(`${API_URL}${path}`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error(`Monitoring API returned ${response.status}`);
        const result = await response.json() as DashboardPayload;
        if (view === "metrics") {
          const metricNames = ["requests", "llm", "agents", "tools", "rag", "database", "system", "cost"];
          const metricResults = await Promise.all(metricNames.map(async (name) => {
            const metricResponse = await fetch(`${API_URL}/api/metrics/${name}?since_minutes=${range}&data_origin=${dataOrigin}`, { signal: controller.signal, cache: "no-store" });
            if (!metricResponse.ok) throw new Error(`Metrics API returned ${metricResponse.status}`);
            return [name, await metricResponse.json()] as const;
          }));
          setPayload(Object.fromEntries(metricResults) as DashboardPayload);
        } else {
          setPayload(result);
        }
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === "AbortError") return;
        setPayload(null);
        setError(loadError instanceof Error ? loadError.message : "Monitoring data could not be loaded.");
      } finally {
        setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [view, traceId, page, range, dataOrigin, query, severity, eventType, refresh]);

  const title = view === "trace-detail" ? "Trace detail" : routes.find((route) => route.key === view)?.label ?? "Observability";
  const headingIcon = view === "traces" || view === "trace-detail" ? GitBranch : view === "logs" ? FileSearch : view === "drift" ? Activity : view === "guardrails" ? ShieldCheck : view === "approvals" ? Siren : view === "metrics" ? Clock3 : Database;
  const HeadingIcon = headingIcon;

  return <main className="obs-shell">
    <header className="obs-topbar"><Link className="obs-back" href="/"><ArrowLeft size={15} /> Case desk</Link><div className="obs-top-meta"><span><i className="obs-live-indicator" />{dataOrigin === "LIVE" ? "LIVE TELEMETRY" : "DEMO DATA"}</span><select aria-label="Telemetry data source" value={dataOrigin} onChange={(event) => { setDataOrigin(event.target.value); setPage(1); }}><option value="LIVE">Live</option><option value="DEMO">Demo</option></select></div></header>
    <div className="obs-content">
      <div className="obs-heading"><div><div className="obs-eyebrow">FULFILLMENT OPERATIONS <span>/</span> PLATFORM HEALTH</div><h1><HeadingIcon size={23} />{title}</h1><p>Correlated runtime telemetry from persisted application events.</p></div><button className="obs-refresh" type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh data</button></div>
      <nav className="obs-nav" aria-label="Observability views">{routes.map((route) => <Link key={route.key} className={route.key === view || (view === "trace-detail" && route.key === "traces") ? "selected" : ""} href={route.href}>{route.label}</Link>)}</nav>
      {view !== "overview" && view !== "trace-detail" && <div className="obs-toolbar"><label className="obs-search">{view === "logs" ? <><FileSearch size={15} /><input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Search structured events" /></> : <span>{view === "metrics" ? "Time window" : "Event window"}</span>}</label>{view === "logs" && <><select aria-label="Severity filter" value={severity} onChange={(event) => { setSeverity(event.target.value); setPage(1); }}><option value="">All levels</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select><select aria-label="Event type filter" value={eventType} onChange={(event) => { setEventType(event.target.value); setPage(1); }}><option value="">All events</option><option value="request.completed">Requests</option><option value="span.ended">Agent spans</option><option value="tool.call">Tool calls</option><option value="llm.call">LLM calls</option><option value="rag.retrieval">RAG</option><option value="guardrail.decision">Guardrails</option></select>{payload?.items && <><button className="obs-export" type="button" onClick={() => downloadEvents(payload.items ?? [], "csv")}><ArrowDownToLine size={13} /> CSV</button><button className="obs-export" type="button" onClick={() => downloadEvents(payload.items ?? [], "json")}>JSON</button></>}</>}{view !== "traces" && view !== "guardrails" && view !== "approvals" && <select aria-label="Time window" value={range} onChange={(event) => setRange(event.target.value)}><option value="60">Last hour</option><option value="1440">Last 24 hours</option><option value="10080">Last 7 days</option></select>}</div>}
      {loading && <div className="obs-state"><span className="obs-spinner" />Loading persisted telemetry…</div>}
      {!loading && error && <div className="obs-state obs-error"><CircleAlert size={18} /><div><strong>Monitoring data unavailable</strong><span>{error}. Configure the backend URL, CORS, and caller authentication.</span></div></div>}
      {!loading && !error && payload && view === "overview" && <>
        <section className="obs-kpis"><Kpi label="Requests" value={number(payload.requests?.total)} detail={`${number(payload.requests?.errors)} errors`} /><Kpi label="Error rate" value={number(typeof payload.requests?.error_rate === "number" ? payload.requests.error_rate * 100 : null, "%")} detail="From request events" /><Kpi label="P95 latency" value={number(payload.requests?.p95_latency_ms, " ms")} detail="Measured HTTP spans" /><Kpi label="LLM calls" value={number(payload.llm?.calls)} detail={`${number(payload.llm?.prompt_tokens)} prompt tokens`} /><Kpi label="Agent spans" value={number(payload.agents?.spans)} detail={`${number(payload.agents?.failures)} failures`} /><Kpi label="Tool calls" value={number(payload.tools?.calls)} detail={`${number(payload.tools?.failures)} failures`} /><Kpi label="RAG retrievals" value={number(payload.rag?.retrievals)} detail={`${number(payload.rag?.no_result)} no-result`} /><Kpi label="Pending approvals" value={number(payload.pending_approvals)} detail={`${number(payload.active_alerts)} active alerts`} /></section>
        <section className="obs-panels"><div className="obs-panel"><div className="obs-panel-title"><div><span>RUNTIME</span><h2>Service health</h2></div><span className="obs-state-tag">Measured only</span></div><HealthLine label="API request telemetry" status={number(payload.requests?.total) === "0" ? "No events" : "Recording"} /><HealthLine label="Database" status={text(payload.database?.status)} /><HealthLine label="Redis" status="Use /api/health" /><HealthLine label="CPU / memory / disk" status={text(payload.infrastructure?.status)} /></div><div className="obs-panel"><div className="obs-panel-title"><div><span>MODEL QUALITY</span><h2>Drift availability</h2></div><Link href="/drift">Details <ArrowUpRight size={13} /></Link></div><div className="obs-empty-inline">No reference windows or verified outcome labels are configured. Drift measures remain unavailable until sufficient real observations exist.</div><HealthLine label="Overall drift" status={text(payload.drift_status)} /></div></section>
      </>}
      {!loading && !error && payload && view === "logs" && <DataSection title={`${number(payload.total)} events`} empty={!payload.items?.length} emptyText="No persisted log events match these filters.">{eventRows(payload.items ?? [], true)}<Pager page={page} pages={Number(payload.pages ?? 0)} onChange={setPage} /></DataSection>}
      {!loading && !error && payload && view === "traces" && <DataSection title={`${number(payload.total)} traces`} empty={!payload.items?.length} emptyText="No traces have been recorded yet."><div className="obs-table-scroll"><table className="obs-table"><thead><tr><th>Trace ID</th><th>Workflow</th><th>Started</th><th>Events</th><th /></tr></thead><tbody>{(payload.items ?? []).map((item) => <tr key={text(item.trace_id)}><td><Link className="obs-trace-link" href={`/traces/${text(item.trace_id)}`}>{text(item.trace_id)} <ArrowUpRight size={12} /></Link></td><td>{text(item.workflow_id)}</td><td>{text(item.started_at)}</td><td>{text(item.event_count)}</td><td><Link href={`/traces/${text(item.trace_id)}`}>Open</Link></td></tr>)}</tbody></table></div><Pager page={page} pages={Number(payload.pages ?? 0)} onChange={setPage} /></DataSection>}
      {!loading && !error && payload && view === "trace-detail" && <DataSection title={`${number(payload.event_count)} trace events`} empty={!payload.spans?.length} emptyText="This trace has no persisted spans."><TraceWaterfall spans={payload.spans ?? []} /></DataSection>}
      {!loading && !error && payload && view === "metrics" && <div className="obs-metric-groups">{Object.entries(payload).map(([name, value]) => { const metric = value as DashboardPayload; return <DataSection key={name} title={name.toUpperCase()} empty={!metric.items?.length && !metric.snapshot} emptyText={name === "cost" ? "No pricing is configured; costs are not estimated." : "No measured events in this window."}>{metric.snapshot && <div className="obs-snapshot">{Object.entries(metric.snapshot).map(([key, entry]) => <div key={key}><span>{key.replaceAll("_", " ")}</span><strong>{text(entry)}</strong></div>)}</div>}{Boolean(metric.items?.length) && <div className="obs-table-scroll"><table className="obs-table"><thead><tr><th>Group</th><th>Calls</th><th>Failures</th><th>Average ms</th><th>P95 ms</th><th>Tokens</th><th>Estimated USD</th></tr></thead><tbody>{(metric.items ?? []).map((item, index) => <tr key={`${name}-${index}`}><td>{text(item.name)}</td><td>{text(item.calls)}</td><td>{text(item.failures)}</td><td>{number(item.average_latency_ms)}</td><td>{number(item.p95_latency_ms)}</td><td>{number(item.tokens)}</td><td>{number(item.estimated_cost_usd)}</td></tr>)}</tbody></table></div>}</DataSection>; })}</div>}
      {!loading && !error && payload && view === "drift" && <div className="obs-drift-grid">{["prediction", "data", "concept", "prompt", "retrieval", "output"].map((name) => { const item = payload[name] as DataRecord | undefined; return <div className="obs-drift-row" key={name}><div><span className={`obs-drift-status drift-${text(item?.status, "unavailable").toLowerCase()}`} />{name} drift</div><strong>{text(item?.status, "UNAVAILABLE")}</strong><span>{item?.score !== undefined ? `Jensen-Shannon ${number(item.score)}` : text(item?.reason, "No qualifying observations")}</span></div>; })}<p className="obs-disclaimer">Drift status is calculated from observed predictions and labeled windows only. Missing baselines and labels are reported as unavailable, not inferred.</p></div>}
      {!loading && !error && payload && view === "guardrails" && <DataSection title={`${number(payload.total)} decisions · ${number(payload.blocked)} blocked`} empty={!payload.items?.length} emptyText="No guardrail events have been recorded.">{eventRows(payload.items ?? [], true)}<Pager page={page} pages={Math.ceil(Number(payload.total ?? 0) / 30)} onChange={setPage} /></DataSection>}
      {!loading && !error && payload && view === "approvals" && <><DataSection title={`${number(payload.pending?.length)} pending`} empty={!payload.pending?.length} emptyText="No workflows are waiting for approval."><div className="obs-table-scroll"><table className="obs-table"><thead><tr><th>Workflow</th><th>Order</th><th>Status</th><th>Reason</th><th>Proposed actions</th></tr></thead><tbody>{(payload.pending ?? []).map((item) => <tr key={text(item.workflow_id)}><td>{text(item.workflow_id)}</td><td>{text(item.order_id)}</td><td>{text(item.status)}</td><td>{text((item.approval as DataRecord | undefined)?.reason)}</td><td>{Array.isArray(item.actions) ? item.actions.map((action) => text((action as DataRecord).action)).join(" · ") : "—"}</td></tr>)}</tbody></table></div></DataSection><DataSection title="Recorded decisions" empty={!payload.decisions?.length} emptyText="No approval decisions have been recorded."><div className="obs-table-scroll"><table className="obs-table"><thead><tr><th>Workflow</th><th>Decision</th><th>Reviewer</th><th>Recorded</th><th>Comment</th></tr></thead><tbody>{(payload.decisions ?? []).map((item, index) => <tr key={`${text(item.workflow_id)}-${index}`}><td>{text(item.workflow_id)}</td><td>{text(item.status)}</td><td>{text(item.reviewer_id)}</td><td>{text(item.created_at)}</td><td>{text(item.comment)}</td></tr>)}</tbody></table></div></DataSection></>}
    </div>
  </main>;
}

function Kpi({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="obs-kpi"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function HealthLine({ label, status }: { label: string; status: string }) {
  return <div className="obs-health-line"><span>{label}</span><strong>{status}</strong></div>;
}

function DataSection({ title, empty, emptyText, children }: { title: string; empty: boolean; emptyText: string; children: React.ReactNode }) {
  return <section className="obs-panel obs-data-panel"><div className="obs-panel-title"><div><span>TELEMETRY</span><h2>{title}</h2></div></div>{empty ? <div className="obs-empty-inline">{emptyText}</div> : children}</section>;
}

function Pager({ page, pages, onChange }: { page: number; pages: number; onChange: (page: number) => void }) {
  return <div className="obs-pager"><span>Page {page} of {pages || 1}</span><div><button disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</button><button disabled={page >= pages} onClick={() => onChange(page + 1)}>Next</button></div></div>;
}

function TraceWaterfall({ spans }: { spans: DataRecord[] }) {
  const ended = new Map(spans.filter((span) => span.event_type === "span.ended").map((span) => [span.span_id, span]));
  const entries = spans.filter((span) => span.event_type === "span.started");
  const byId = new Map(entries.map((entry) => [String(entry.span_id), entry]));
  return <div className="trace-waterfall">{entries.map((entry) => {
    const result = ended.get(entry.span_id);
    let parentId = text(entry.parent_span_id, "");
    let depth = 0;
    while (parentId && byId.has(parentId) && depth < 6) {
      depth += 1;
      parentId = text(byId.get(parentId)?.parent_span_id, "");
    }
    const duration = Number(result?.latency_ms ?? 0);
    return <div className="trace-span" key={text(entry.span_id)} style={{ paddingLeft: `${depth * 20}px` }}><span className="trace-span-name">{text((entry.details as DataRecord | undefined)?.span_name, text(entry.agent_name ?? entry.tool_name))}</span><span className="trace-span-track"><i style={{ width: `${Math.max(4, Math.min(100, duration))}%` }} /></span><span className="obs-mono">{number(duration, " ms")}</span><span>{text(result?.status, "running")}</span></div>;
  })}</div>;
}