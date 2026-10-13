"use client";

import {
  Activity,
  ArrowDownUp,
  ArrowRight,
  BadgeCheck,
  Bell,
  Boxes,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  Clock3,
  ExternalLink,
  FileClock,
  FileText,
  Filter,
  Gauge,
  MapPin,
  PackageCheck,
  Plus,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Truck,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";

type Citation = {
  document_id?: string;
  title?: string;
  source?: string;
  section?: string;
  page?: number;
  quote?: string;
};

type CaseReport = {
  workflow_id: string;
  status: string;
  order: { order_id: string; customer_name?: string; fulfillment_center?: string; promised_delivery?: string; order_total?: number };
  summary: string;
  classification: { failure_reason: string; root_cause_category: string; evidence: string[]; confidence: number };
  evidence?: string[];
  confidence: number;
  actions: { action_id?: string; action: string; status: string; reference?: string }[];
  approval: { required: boolean; status: string; reason?: string };
  citations: Citation[];
  validation?: { decision: string; issues: string[] };
  generated_at?: string;
};

type DashboardData = { metrics: Record<string, number>; investigations: CaseReport[] };

const API_URL = "/api/backend";

function shortDate(value?: string) {
  if (!value) return "Just now";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function statusLabel(status: string) {
  if (status === "awaiting_approval") return "Review needed";
  if (status === "completed") return "Resolved";
  return status.replaceAll("_", " ");
}

export default function Home() {
  const [cases, setCases] = useState<CaseReport[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [metrics, setMetrics] = useState({ investigations: 0, awaiting_approval: 0, high_confidence: 0, sla_risk: 0 });
  const [isConnected, setIsConnected] = useState(false);
  const [query, setQuery] = useState("");
  const [orderId, setOrderId] = useState("");
  const [showNewCase, setShowNewCase] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState("All cases");
  const [search, setSearch] = useState("");
  const [approvalComment, setApprovalComment] = useState("");
  const [approvedActionIds, setApprovedActionIds] = useState<string[]>([]);
  const [loadError, setLoadError] = useState("");

  const selectedCase = cases.find((item) => item.workflow_id === selectedId) ?? cases[0];
  const visibleCases = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return cases.filter((item) => {
      const matchesFilter = filter === "All cases" || (filter === "Needs review" ? item.approval.status === "pending" : item.status === "completed");
      const matchesSearch = !normalizedSearch || `${item.order.order_id} ${item.classification.failure_reason} ${item.order.customer_name ?? ""}`.toLowerCase().includes(normalizedSearch);
      return matchesFilter && matchesSearch;
    });
  }, [cases, filter, search]);

  useEffect(() => {
    fetch(`${API_URL}/api/dashboard`)
      .then((response) => {
        if (!response.ok) throw new Error("API unavailable");
        return response.json() as Promise<DashboardData>;
      })
      .then((data) => {
        setIsConnected(true);
        setLoadError("");
        setCases(data.investigations);
        setMetrics((current) => ({ ...current, ...data.metrics }));
        if (data.investigations[0]?.workflow_id) setSelectedId(data.investigations[0].workflow_id);
      })
      .catch(() => {
        setIsConnected(false);
        setLoadError("Live case data is unavailable. Check the API connection.");
      });
  }, []);

  async function submitInvestigation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!query.trim()) return;
    setIsSubmitting(true);
    setNotice("");
    try {
      const response = await fetch(`${API_URL}/api/investigations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim(), order_id: orderId.trim() || undefined }),
      });
      if (!response.ok) throw new Error("The investigation service could not complete this request.");
      const report = (await response.json()) as CaseReport;
      setCases((current) => [report, ...current.filter((item) => item.workflow_id !== report.workflow_id)]);
      setSelectedId(report.workflow_id);
      setMetrics((current) => ({ ...current, investigations: current.investigations + 1 }));
      setShowNewCase(false);
      setQuery("");
      setOrderId("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not start investigation.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function submitApproval(decision: "approved" | "rejected") {
    if (!selectedCase) return;
    setNotice("");
    try {
      const response = await fetch(`${API_URL}/api/investigations/${selectedCase.workflow_id}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, comment: approvalComment, approved_action_ids: decision === "approved" ? approvedActionIds : [] }),
      });
      if (!response.ok) throw new Error("Approval could not be recorded.");
      const report = (await response.json()) as CaseReport;
      setCases((current) => current.map((item) => item.workflow_id === report.workflow_id ? report : item));
      setApprovalComment("");
      setApprovedActionIds([]);
      setNotice(decision === "approved" ? "Recommendation approved and actions executed." : "Recommendation rejected. No actions were executed.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Approval could not be recorded.");
    }
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" aria-label="Fulfillment Control home">
          <span className="brand-mark"><Boxes size={19} strokeWidth={2.2} /></span>
          <span className="brand-name">fulfill<span> / </span>control</span>
        </a>
        <div className="workspace-switcher">
          <span className="workspace-icon">N</span>
          <span className="workspace-copy"><strong>Northstar Retail</strong><small>Operations workspace</small></span>
          <ChevronDown size={15} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav className="primary-nav" aria-label="Main navigation">
          <a className="nav-item active" href="#overview"><Activity size={17} /><span>Case desk</span><span className="nav-count">{metrics.investigations}</span></a>
          <a className="nav-item" href="#investigations" onClick={() => setFilter("Needs review")}><ShieldAlert size={17} /><span>Needs review</span><span className="nav-count alert-count">{metrics.awaiting_approval}</span></a>
          <a className="nav-item" href="#investigations"><Truck size={17} /><span>Shipment exceptions</span></a>
          <a className="nav-item" href="#evidence"><FileText size={17} /><span>Policy library</span></a>
          <a className="nav-item" href="/observability"><Gauge size={17} /><span>Observability</span></a>
        </nav>
        <div className="sidebar-spacer" />
        <div className="agent-status">
          <span className={`live-dot ${isConnected ? "connected" : ""}`} />
          <div><strong>{isConnected ? "Investigation agents online" : "Demo workspace"}</strong><small>{isConnected ? "All systems operational" : "Connect API to enable live cases"}</small></div>
          <span className="agent-pulse"><span /><span /><span /></span>
        </div>
        <button className="profile-button" type="button">
          <span className="avatar">JL</span><span className="profile-copy"><strong>Jamie Lin</strong><small>Operations analyst</small></span><ChevronDown size={15} />
        </button>
      </aside>

      <section className="workspace" id="overview">
        <header className="topbar">
          <div className="breadcrumbs"><span>Operations</span><span className="crumb-divider">/</span><strong>Case desk</strong></div>
          <div className="topbar-actions">
            <span className="environment-tag"><span className={`live-dot ${isConnected ? "connected" : ""}`} />{isConnected ? "LIVE" : "DEMO"}</span>
            <button className="icon-button" type="button" aria-label="Search cases" title="Search cases" onClick={() => document.getElementById("case-search")?.focus()}><Search size={17} /></button>
            <button className="icon-button notification-button" type="button" aria-label="Notifications" title="Notifications"><Bell size={17} /><i /></button>
            <span className="topbar-date">Tue, Oct 13</span>
          </div>
        </header>

        <div className="page-content">
          <section className="page-heading">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" />FULFILLMENT OPERATIONS <span className="eyebrow-separator">/</span> MONDAY SHIFT</div>
              <h1>Case desk<span className="heading-period">.</span></h1>
              <p>Failure investigations, grounded in operational evidence.</p>
            </div>
            <button className="primary-button" type="button" onClick={() => { setShowNewCase(true); setNotice(""); }}><Plus size={17} /> New investigation <span className="button-shortcut">N</span></button>
          </section>

          <section className="metrics-grid" aria-label="Operations summary">
            <div className="metric-card metric-active"><div className="metric-top"><span>INVESTIGATIONS</span><Activity size={16} /></div><div className="metric-value">{metrics.investigations.toString().padStart(2, "0")}<span className="metric-change">Live records</span></div><div className="metric-foot"><span className="metric-foot-dot green" />All tracked cases</div></div>
            <div className="metric-card"><div className="metric-top"><span>AWAITING REVIEW</span><ShieldAlert size={16} /></div><div className="metric-value">{metrics.awaiting_approval.toString().padStart(2, "0")}<span className="metric-change warning">Needs attention</span></div><div className="metric-foot"><span className="metric-foot-dot amber" />Human decision required</div></div>
            <div className="metric-card"><div className="metric-top"><span>HIGH CONFIDENCE</span><BadgeCheck size={16} /></div><div className="metric-value">{metrics.high_confidence.toString().padStart(2, "0")}<span className="metric-change">of {metrics.investigations.toString().padStart(2, "0")} cases</span></div><div className="metric-foot"><span className="metric-foot-dot green" />Classification ≥ 80%</div></div>
            <div className="metric-card metric-sla"><div className="metric-top"><span>SLA BREACH CLASSIFICATIONS</span><Clock3 size={16} /></div><div className="metric-value">{metrics.sla_risk.toString().padStart(2, "0")}<span className="metric-change risk">Classified</span></div><div className="metric-foot"><span className="metric-foot-dot red" />From persisted reports</div></div>
          </section>

          <section className="case-desk" id="investigations">
            <div className="case-list-panel">
              <div className="list-heading"><div><span className="section-kicker">QUEUE <span>/{visibleCases.length.toString().padStart(2, "0")}</span></span><h2>Investigations</h2></div><button className="icon-button subtle" type="button" title="Filter investigations" aria-label="Filter investigations" onClick={() => setFilter(filter === "All cases" ? "Needs review" : "All cases")}><Filter size={17} /></button></div>
              <div className="list-controls">
                <label className="search-field"><Search size={15} /><input id="case-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Order, reason, customer" /></label>
                <button className="filter-select" type="button" onClick={() => setFilter(filter === "All cases" ? "Needs review" : "All cases")}>{filter}<ChevronDown size={14} /></button>
              </div>
              <div className="case-list">
                {loadError && <div className="empty-list" role="status">{loadError}</div>}
                {!loadError && visibleCases.length === 0 && <div className="empty-list">No live investigations recorded.</div>}
                {visibleCases.map((item) => (
                  <button className={`case-row ${selectedId === item.workflow_id ? "selected" : ""}`} key={item.workflow_id} type="button" onClick={() => { setSelectedId(item.workflow_id); setNotice(""); }}>
                    <div className="case-row-top"><span className={`case-status-dot ${item.approval.status === "pending" ? "pending" : "resolved"}`} /><span className="case-id">{item.order.order_id}</span><span className="case-age">{shortDate(item.generated_at)}</span></div>
                    <div className="case-row-title">{item.classification.failure_reason}</div>
                    <div className="case-row-bottom"><span className={`status-pill ${item.approval.status === "pending" ? "pill-review" : "pill-resolved"}`}>{item.approval.status === "pending" ? <ShieldAlert size={11} /> : <Check size={11} />}{statusLabel(item.status)}</span><span className="case-confidence">{Math.round(item.confidence * 100)}% confidence</span></div>
                    <span className="case-row-arrow"><ArrowRight size={15} /></span>
                  </button>
                ))}
              </div>
              <button className="view-queue-button" type="button" onClick={() => setFilter("All cases")}>View all investigations <ArrowRight size={15} /></button>
            </div>

            {selectedCase && <article className="report-panel">
              <div className="report-topline"><div className="report-breadcrumb"><span>INVESTIGATION</span><span>/</span><span>{selectedCase.workflow_id.slice(0, 8).toUpperCase()}</span></div><div className="report-tools"><span className={`status-pill ${selectedCase.approval.status === "pending" ? "pill-review" : "pill-resolved"}`}>{selectedCase.approval.status === "pending" ? <ShieldAlert size={11} /> : <Check size={11} />}{statusLabel(selectedCase.status)}</span><button className="icon-button subtle" type="button" aria-label="Sort report" title="Report options"><ArrowDownUp size={15} /></button></div></div>
              <div className="report-title-block"><div className="report-overline">ROOT CAUSE CLASSIFICATION</div><h2>{selectedCase.classification.failure_reason}</h2><p>{selectedCase.summary}</p></div>
              <div className="report-meta-row"><div className="order-identity"><span className="order-icon"><PackageCheck size={18} /></span><div><span className="meta-label">ORDER</span><strong>{selectedCase.order.order_id}</strong></div></div><span className="meta-divider" /><div className="order-identity"><span className="meta-icon"><MapPin size={16} /></span><div><span className="meta-label">FULFILLMENT CENTER</span><strong>{selectedCase.order.fulfillment_center ?? "FC-West-04"}</strong></div></div><span className="meta-divider" /><div className="order-identity"><span className="meta-icon"><FileClock size={16} /></span><div><span className="meta-label">PROMISED DELIVERY</span><strong>{selectedCase.order.promised_delivery ?? "Oct 12, 2026"}</strong></div></div></div>

              <div className="report-section evidence-section" id="evidence"><div className="section-title-row"><div><span className="section-kicker">01 / INVESTIGATION</span><h3>Evidence reviewed</h3></div><span className="source-count">{selectedCase.evidence?.length ?? selectedCase.classification.evidence.length} sources</span></div><div className="evidence-list">{(selectedCase.evidence ?? selectedCase.classification.evidence).map((item, index) => <div className="evidence-item" key={`${selectedId}-evidence-${index}`}><span className="evidence-index">0{index + 1}</span><span className="evidence-mark"><CheckCheck size={15} /></span><p>{item}</p><span className="evidence-kind">{index === 0 ? "CARRIER SCAN" : "OPERATIONAL LOG"}</span></div>)}</div></div>

              <div className="report-section classification-section"><div className="section-title-row"><div><span className="section-kicker">02 / MODEL ASSESSMENT</span><h3>Classification confidence</h3></div><span className="confidence-value">{Math.round(selectedCase.confidence * 100)}<small>%</small></span></div><div className="confidence-track"><span style={{ width: `${Math.round(selectedCase.confidence * 100)}%` }} /></div><div className="confidence-labels"><span>LOW CONFIDENCE</span><span>REVIEW THRESHOLD 72%</span><span>HIGH CONFIDENCE</span></div><div className="confidence-note"><Sparkles size={14} /><span>Reasoned from {selectedCase.classification.root_cause_category.replaceAll("_", " ")} signals, corroborated by evidence and policy.</span></div></div>

              <div className="report-section citation-section"><div className="section-title-row"><div><span className="section-kicker">03 / POLICY GROUNDED</span><h3>Relevant procedures</h3></div><button className="text-link" type="button" onClick={() => setNotice("Policy library connected to the current investigation.")}>Policy library <ExternalLink size={13} /></button></div><div className="citation-list">{selectedCase.citations.length ? selectedCase.citations.map((citation, index) => <div className="citation-item" key={`${citation.document_id}-${index}`}><span className="citation-icon"><FileText size={15} /></span><div className="citation-content"><strong>{citation.title ?? citation.source}</strong><span>{citation.section} <i>·</i> {citation.source}{citation.page ? ` · p. ${citation.page}` : ""}</span><p>“{citation.quote}”</p></div><span className="citation-number">[{index + 1}]</span></div>) : <div className="citation-empty">No policy excerpts were returned for this investigation.</div>}</div></div>

              {selectedCase.approval.status === "pending" && <div className="approval-box"><div className="approval-icon"><ShieldAlert size={17} /></div><div className="approval-copy"><strong>Human review required</strong><span>{selectedCase.approval.reason ?? "Select the exact proposed actions to authorize."}</span></div><div className="approval-actions">{selectedCase.actions.map((action) => <label key={action.action_id ?? action.action}><input type="checkbox" checked={Boolean(action.action_id && approvedActionIds.includes(action.action_id))} onChange={(event) => { if (!action.action_id) return; setApprovedActionIds((current) => event.target.checked ? [...current, action.action_id!] : current.filter((id) => id !== action.action_id)); }} />{action.action}</label>)}</div><textarea aria-label="Reviewer note" value={approvalComment} onChange={(event) => setApprovalComment(event.target.value)} placeholder="Add a review note (optional)" rows={1} /><button className="reject-button" type="button" onClick={() => submitApproval("rejected")}>Reject</button><button className="approve-button" type="button" disabled={!approvedActionIds.length} onClick={() => submitApproval("approved")}><Check size={14} /> Approve selected</button></div>}

              <div className="report-section actions-section"><div className="section-title-row"><div><span className="section-kicker">04 / FOLLOW-THROUGH</span><h3>Recommended actions</h3></div><span className="action-count">{selectedCase.actions.length} actions</span></div><div className="action-list">{selectedCase.actions.map((action, index) => <div className="action-item" key={`${action.action}-${index}`}><span className={`action-check ${action.status === "executed" ? "done" : ""}`}>{action.status === "executed" ? <Check size={13} /> : <span>{index + 1}</span>}</span><span className="action-name">{action.action}</span><span className={`action-state ${action.status === "executed" ? "state-done" : ""}`}>{action.reference ?? action.status}</span></div>)}</div></div>

              <footer className="report-footer"><span><ShieldCheck size={14} /> Evidence validated · {selectedCase.validation?.decision ?? "PASS"}</span><span>Generated {shortDate(selectedCase.generated_at)}</span></footer>
            </article>}
          </section>
          {notice && <div className="notice-toast" role="status"><span>{notice}</span><button type="button" aria-label="Dismiss" onClick={() => setNotice("")}><X size={15} /></button></div>}
        </div>
      </section>

      {showNewCase && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowNewCase(false); }}><section className="new-case-modal" role="dialog" aria-modal="true" aria-labelledby="new-case-title"><header className="modal-header"><div><span className="section-kicker">CASE INTAKE / 01</span><h2 id="new-case-title">Start an investigation</h2><p>Describe the fulfillment exception. Agents will gather evidence across order, inventory, shipment, and policy systems.</p></div><button className="icon-button subtle" type="button" aria-label="Close" onClick={() => setShowNewCase(false)}><X size={18} /></button></header><form onSubmit={submitInvestigation}><label className="form-label" htmlFor="order-id">ORDER ID <span>OPTIONAL</span></label><input id="order-id" className="form-input" value={orderId} onChange={(event) => setOrderId(event.target.value)} placeholder="ORD-10482" /><label className="form-label" htmlFor="incident-query">WHAT HAPPENED? <span>REQUIRED</span></label><textarea id="incident-query" className="form-textarea" required minLength={3} maxLength={4000} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. Customer reports the parcel is late. Tracking shows a weather exception at the regional hub." rows={5} /><div className="form-hint"><CircleHelp size={14} /> Include known order details, timestamps, or exception codes.</div>{notice && <div className="form-error" role="alert">{notice}</div>}<div className="modal-footer"><span><ShieldCheck size={14} /> Evidence-backed · Human review when uncertain</span><button className="primary-button" type="submit" disabled={isSubmitting}>{isSubmitting ? <><span className="spinner" /> Investigating</> : <>Run investigation <ArrowRight size={16} /></>}</button></div></form></section></div>}
    </main>
  );
}