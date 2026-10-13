from datetime import datetime, timedelta, timezone
from math import ceil, log2
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, text

from app.database import SessionLocal, engine
from app.models import AlertEvent, Approval, DriftObservation, FulfillmentFailure, TelemetryEvent, Workflow
from app.security import current_principal
from fastapi import Depends

router = APIRouter(prefix="/api", dependencies=[Depends(current_principal)])
_EVENT_TYPES = {"requests": "request.completed", "llm": "llm.call", "agents": "span.ended", "tools": "tool.call", "rag": "rag.retrieval"}
_SORT_FIELDS = {"time": TelemetryEvent.created_at, "latency": TelemetryEvent.latency_ms, "severity": TelemetryEvent.severity, "event": TelemetryEvent.event_type}


def _system_snapshot() -> dict:
    try:
        import psutil
        process = psutil.Process()
        return {"status": "available", "scope": "api_process", "cpu_percent": process.cpu_percent(interval=0.05), "rss_bytes": process.memory_info().rss, "thread_count": process.num_threads(), "host_memory_percent": None, "host_disk_percent": None}
    except (ImportError, OSError):
        return {"status": "unavailable", "scope": "api_process", "cpu_percent": None, "rss_bytes": None, "thread_count": None, "host_memory_percent": None, "host_disk_percent": None}


def _database_snapshot() -> dict:
    started = perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            size_bytes = None
            if engine.dialect.name == "postgresql":
                size_bytes = connection.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
            elif engine.dialect.name == "sqlite":
                db_path = engine.url.database
                size_bytes = Path(db_path).stat().st_size if db_path and Path(db_path).exists() else None
        return {"status": "connected", "query_latency_ms": round((perf_counter() - started) * 1000, 3), "size_bytes": size_bytes, "pool_checked_out": engine.pool.checkedout() if hasattr(engine.pool, "checkedout") else None}
    except Exception:
        return {"status": "unavailable", "query_latency_ms": None, "size_bytes": None, "pool_checked_out": None}


def _event_json(event: TelemetryEvent) -> dict:
    return {
        "id": event.event_id,
        "timestamp": event.created_at.isoformat() if event.created_at else None,
        "environment": event.environment,
        "service": event.service,
        "event_type": event.event_type,
        "severity": event.severity,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "workflow_id": event.workflow_id,
        "agent_name": event.agent_name,
        "tool_name": event.tool_name,
        "model_name": event.model_name,
        "status": event.status,
        "latency_ms": event.latency_ms,
        "data_origin": event.data_origin,
        "details": event.details or {},
    }


def _base_query(session, data_origin: str):
    return session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == data_origin)


@router.get("/observability/overview")
def observability_overview(data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$")):
    with SessionLocal() as session:
        events = _base_query(session, data_origin)
        request_events = events.filter(TelemetryEvent.event_type == "request.completed")
        total_requests = request_events.count()
        failed_requests = request_events.filter(TelemetryEvent.status.in_(["error", "500", "502", "503", "504"])).count()
        request_latencies = [value for (value,) in request_events.with_entities(TelemetryEvent.latency_ms).filter(TelemetryEvent.latency_ms.isnot(None)).order_by(TelemetryEvent.created_at.desc()).limit(5000).all()]
        sorted_latencies = sorted(request_latencies)
        p95 = sorted_latencies[min(len(sorted_latencies) - 1, ceil(len(sorted_latencies) * 0.95) - 1)] if sorted_latencies else None
        counts = dict(session.query(TelemetryEvent.event_type, func.count(TelemetryEvent.id)).filter(TelemetryEvent.data_origin == data_origin).group_by(TelemetryEvent.event_type).all())
        active_alerts = session.query(AlertEvent).filter(AlertEvent.data_origin == data_origin, AlertEvent.status == "active").count()
        pending_approvals = session.query(Workflow).filter(Workflow.status == "awaiting_approval").count() if data_origin == "LIVE" else 0
        infrastructure = _system_snapshot() if data_origin == "LIVE" else {"status": "unavailable", "reason": "The demo seed contains no measured process or host resource metrics."}
        database = _database_snapshot() if data_origin == "LIVE" else {"status": "unavailable", "reason": "A demo record is not a database health measurement."}
        return {
            "data_origin": data_origin,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "requests": {"total": total_requests, "errors": failed_requests, "error_rate": failed_requests / total_requests if total_requests else None, "average_latency_ms": sum(request_latencies) / len(request_latencies) if request_latencies else None, "p95_latency_ms": p95},
            "llm": {"calls": counts.get("llm.call", 0), "errors": counts.get("llm.error", 0), "prompt_tokens": None, "completion_tokens": None, "estimated_cost_usd": None},
            "agents": {"spans": counts.get("span.ended", 0), "failures": session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == data_origin, TelemetryEvent.event_type == "span.ended", TelemetryEvent.status == "error").count()},
            "tools": {"calls": counts.get("tool.call", 0), "failures": session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == data_origin, TelemetryEvent.event_type == "tool.call", TelemetryEvent.status == "error").count()},
            "rag": {"retrievals": counts.get("rag.retrieval", 0), "no_result": session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == data_origin, TelemetryEvent.event_type == "rag.retrieval", TelemetryEvent.status == "no_results").count()},
            "guardrails": {"checks": counts.get("guardrail.decision", 0), "blocked": session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == data_origin, TelemetryEvent.event_type == "guardrail.decision", TelemetryEvent.status == "BLOCK").count()},
            "pending_approvals": pending_approvals,
            "active_alerts": active_alerts,
            "infrastructure": infrastructure,
            "database": database,
            "drift_status": "UNAVAILABLE",
        }


@router.get("/logs")
def list_logs(
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=50, ge=1, le=200),
    event_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    service: str | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
    model_name: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    workflow_id: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    since_minutes: int = Query(default=1440, ge=1, le=10080),
    sort_by: str = Query(default="time", pattern="^(time|latency|severity|event)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$"),
):
    with SessionLocal() as session:
        query = _base_query(session, data_origin).filter(TelemetryEvent.created_at >= datetime.now(timezone.utc) - timedelta(minutes=since_minutes))
        for column, value in [
            (TelemetryEvent.event_type, event_type), (TelemetryEvent.severity, severity.upper() if severity else None),
            (TelemetryEvent.status, status), (TelemetryEvent.service, service), (TelemetryEvent.agent_name, agent_name),
            (TelemetryEvent.tool_name, tool_name), (TelemetryEvent.model_name, model_name), (TelemetryEvent.request_id, request_id),
            (TelemetryEvent.trace_id, trace_id), (TelemetryEvent.workflow_id, workflow_id),
        ]:
            if value:
                query = query.filter(column == value)
        if search:
            query = query.filter(or_(TelemetryEvent.event_type.ilike(f"%{search}%"), TelemetryEvent.details.cast(str).ilike(f"%{search}%")))
        total = query.count()
        sort_column = _SORT_FIELDS[sort_by]
        sort_column = sort_column.asc().nullslast() if order == "asc" else sort_column.desc().nullslast()
        entries = query.order_by(sort_column, TelemetryEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [_event_json(event) for event in entries], "page": page, "page_size": page_size, "total": total, "pages": ceil(total / page_size) if total else 0, "data_origin": data_origin}


@router.get("/logs/{event_id}")
def get_log(event_id: str):
    with SessionLocal() as session:
        event = session.query(TelemetryEvent).filter_by(event_id=event_id).one_or_none()
        if event is None:
            raise HTTPException(status_code=404, detail="Log event not found")
        return _event_json(event)


@router.get("/traces")
def list_traces(page: int = Query(default=1, ge=1), page_size: int = Query(default=40, ge=1, le=200), data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$")):
    with SessionLocal() as session:
        rows = session.query(
            TelemetryEvent.trace_id,
            func.min(TelemetryEvent.created_at),
            func.max(TelemetryEvent.created_at),
            func.count(TelemetryEvent.id),
            func.max(TelemetryEvent.workflow_id),
        ).filter(TelemetryEvent.data_origin == data_origin, TelemetryEvent.trace_id.isnot(None)).group_by(TelemetryEvent.trace_id).order_by(func.max(TelemetryEvent.created_at).desc())
        total = rows.count()
        items = rows.offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [{"trace_id": trace_id, "started_at": start.isoformat() if start else None, "last_event_at": end.isoformat() if end else None, "event_count": count, "workflow_id": workflow_id} for trace_id, start, end, count, workflow_id in items], "page": page, "page_size": page_size, "total": total, "pages": ceil(total / page_size) if total else 0, "data_origin": data_origin}


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str):
    with SessionLocal() as session:
        events = session.query(TelemetryEvent).filter_by(trace_id=trace_id).order_by(TelemetryEvent.created_at.asc()).all()
        if not events:
            raise HTTPException(status_code=404, detail="Trace not found")
        return {"trace_id": trace_id, "spans": [_event_json(event) for event in events], "event_count": len(events)}


@router.get("/metrics/{metric}")
def get_metrics(metric: str, since_minutes: int = Query(default=1440, ge=1, le=10080), data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$")):
    if metric not in {*_EVENT_TYPES, "system", "database", "cost"}:
        raise HTTPException(status_code=404, detail="Metric category not found")
    if metric == "system":
        return {"metric": "system", "data_origin": data_origin, "snapshot": _system_snapshot() if data_origin == "LIVE" else {"status": "unavailable", "reason": "No demo host resource measurements are available."}, "items": []}
    if metric == "database":
        return {"metric": "database", "data_origin": data_origin, "snapshot": _database_snapshot() if data_origin == "LIVE" else {"status": "unavailable", "reason": "Demo telemetry is not a database health measurement."}, "items": []}
    with SessionLocal() as session:
        event_type = _EVENT_TYPES.get(metric)
        query = _base_query(session, data_origin).filter(TelemetryEvent.created_at >= datetime.now(timezone.utc) - timedelta(minutes=since_minutes))
        if event_type:
            query = query.filter(TelemetryEvent.event_type == event_type)
        if metric == "agents":
            query = query.filter(TelemetryEvent.agent_name.isnot(None))
        if metric == "system":
            query = query.filter(TelemetryEvent.event_type.in_(["system.metric", "database.metric"]))
        if metric == "database":
            query = query.filter(TelemetryEvent.event_type == "database.metric")
        if metric == "cost":
            query = query.filter(TelemetryEvent.event_type == "llm.call")
        rows = query.order_by(TelemetryEvent.created_at.desc()).limit(5000).all()
        grouped: dict[str, list[TelemetryEvent]] = {}
        for row in rows:
            key = row.agent_name or row.tool_name or row.model_name or row.event_type
            grouped.setdefault(key, []).append(row)
        summaries = []
        for name, group in sorted(grouped.items()):
            latencies = sorted(row.latency_ms for row in group if row.latency_ms is not None)
            token_values = [row.details.get("total_tokens") for row in group if row.details and isinstance(row.details.get("total_tokens"), (float, int))]
            cost_values = [row.details.get("estimated_cost_usd") for row in group if row.details and isinstance(row.details.get("estimated_cost_usd"), (float, int))]
            summaries.append({
                "name": name,
                "calls": len(group),
                "failures": sum(row.status in {"error", "failed", "invalid_output"} for row in group),
                "average_latency_ms": sum(latencies) / len(latencies) if latencies else None,
                "p95_latency_ms": latencies[min(len(latencies) - 1, ceil(len(latencies) * .95) - 1)] if latencies else None,
                "tokens": sum(token_values) if token_values else None,
                "estimated_cost_usd": sum(cost_values) if cost_values else None,
            })
        return {"metric": metric, "since_minutes": since_minutes, "data_origin": data_origin, "count": len(rows), "items": summaries}


def _js_divergence(first: dict[str, int], second: dict[str, int]) -> float:
    labels = set(first) | set(second)
    first_total, second_total = sum(first.values()), sum(second.values())
    if not first_total or not second_total:
        raise ValueError("Both drift windows require labeled records")
    p = {label: first.get(label, 0) / first_total for label in labels}
    q = {label: second.get(label, 0) / second_total for label in labels}
    m = {label: (p[label] + q[label]) / 2 for label in labels}
    return sum(p[label] * log2(p[label] / m[label]) / 2 for label in labels if p[label]) + sum(q[label] * log2(q[label] / m[label]) / 2 for label in labels if q[label])


@router.get("/drift")
def drift_summary(data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$")):
    now = datetime.now(timezone.utc)
    current_start, reference_start = now - timedelta(days=14), now - timedelta(days=28)
    with SessionLocal() as session:
        if data_origin == "DEMO":
            samples = session.query(DriftObservation).filter_by(data_origin="DEMO").order_by(DriftObservation.created_at.desc()).all()
            latest = samples[0] if samples else None
            return {
                "overall_status": latest.status if latest else "UNAVAILABLE",
                "data_origin": "DEMO",
                "prediction": {"status": latest.status if latest else "UNAVAILABLE", "method": "Jensen-Shannon divergence (illustrative seeded sample)", "score": latest.score if latest else None, "reference_count": None, "current_count": None},
                "data": {"status": "UNAVAILABLE", "reason": "The demo seed contains no feature-level reference samples."},
                "concept": {"status": "UNAVAILABLE", "reason": "Verified outcome labels are not available."},
                "prompt": {"status": "UNAVAILABLE", "reason": "Versioned prompt baseline comparison is not configured."},
                "retrieval": {"status": "UNAVAILABLE", "reason": "Labeled retrieval relevance data is not available."},
                "output": {"status": "UNAVAILABLE", "reason": "Groundedness reference evaluations are not configured."},
                "window": {"reference_start": latest.reference_window if latest else None, "current_start": latest.current_window if latest else None, "end": latest.created_at.isoformat() if latest and latest.created_at else None},
            }
        current_rows = session.query(FulfillmentFailure.failure_reason, func.count(FulfillmentFailure.id)).filter(FulfillmentFailure.created_at >= current_start).group_by(FulfillmentFailure.failure_reason).all()
        reference_rows = session.query(FulfillmentFailure.failure_reason, func.count(FulfillmentFailure.id)).filter(FulfillmentFailure.created_at >= reference_start, FulfillmentFailure.created_at < current_start).group_by(FulfillmentFailure.failure_reason).all()
        current, reference = dict(current_rows), dict(reference_rows)
        sufficient = sum(current.values()) >= 10 and sum(reference.values()) >= 10
        score = _js_divergence(reference, current) if sufficient else None
        prediction_status = "UNAVAILABLE" if not sufficient else "CRITICAL" if score >= .25 else "WARNING" if score >= .1 else "STABLE"
        return {
            "overall_status": prediction_status,
            "data_origin": "LIVE",
            "prediction": {"status": prediction_status, "method": "Jensen-Shannon divergence", "score": score, "reference_count": sum(reference.values()), "current_count": sum(current.values()), "reference_distribution": reference, "current_distribution": current},
            "data": {"status": "UNAVAILABLE", "reason": "Operational feature reference distributions are not configured."},
            "concept": {"status": "UNAVAILABLE", "reason": "Verified outcome labels are not available."},
            "prompt": {"status": "UNAVAILABLE", "reason": "Versioned prompt baseline comparison is not configured."},
            "retrieval": {"status": "UNAVAILABLE", "reason": "Labeled retrieval relevance data is not available."},
            "output": {"status": "UNAVAILABLE", "reason": "Groundedness reference evaluations are not configured."},
            "window": {"reference_start": reference_start.isoformat(), "current_start": current_start.isoformat(), "end": now.isoformat()},
        }


@router.get("/drift/{drift_type}")
def drift_by_type(drift_type: str, data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$")):
    names = {"data", "model", "prediction", "concept", "prompt", "retrieval", "output"}
    if drift_type not in names:
        raise HTTPException(status_code=404, detail="Drift category not found")
    summary = drift_summary(data_origin)
    selected = summary.get("prediction") if drift_type == "model" else summary.get(drift_type)
    return {"drift_type": drift_type, "data_origin": summary["data_origin"], "overall_status": summary["overall_status"], **(selected or {"status": "UNAVAILABLE", "reason": "No measurement is configured."})}


@router.get("/guardrails")
def guardrail_events(page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200), data_origin: str = Query(default="LIVE", pattern="^(LIVE|DEMO)$")):
    with SessionLocal() as session:
        query = _base_query(session, data_origin).filter(TelemetryEvent.event_type.in_(["guardrail.decision", "security.rate_limit"]))
        total = query.count()
        rows = query.order_by(TelemetryEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        blocked = query.filter(TelemetryEvent.status == "BLOCK").count()
        return {"items": [_event_json(event) for event in rows], "page": page, "page_size": page_size, "total": total, "blocked": blocked, "data_origin": data_origin}


@router.get("/security")
def security_events(page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    with SessionLocal() as session:
        query = _base_query(session, "LIVE").filter(TelemetryEvent.event_type.in_(["security.rate_limit", "guardrail.decision"]))
        total = query.count()
        rows = query.order_by(TelemetryEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [_event_json(event) for event in rows], "page": page, "page_size": page_size, "total": total}


@router.get("/errors")
def error_events(page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    with SessionLocal() as session:
        query = _base_query(session, "LIVE").filter(TelemetryEvent.severity.in_(["ERROR", "CRITICAL"]))
        total = query.count()
        rows = query.order_by(TelemetryEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [_event_json(event) for event in rows], "page": page, "page_size": page_size, "total": total}


@router.get("/approvals")
def approvals(page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    with SessionLocal() as session:
        pending = session.query(Workflow).filter(Workflow.status == "awaiting_approval").order_by(Workflow.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        decisions = session.query(Approval).order_by(Approval.created_at.desc()).limit(page_size).all()
        return {"pending": [{"workflow_id": state.workflow_id, "order_id": state.order_id, "status": state.status, "approval": state.state.get("human_approval", {}), "actions": state.state.get("proposed_actions", [])} for state in pending], "decisions": [{"workflow_id": row.workflow_id, "status": row.status, "reviewer_id": row.reviewer_id, "comment": row.comment, "created_at": row.created_at.isoformat() if row.created_at else None} for row in decisions], "page": page, "page_size": page_size}


@router.get("/alerts")
def alerts(page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=200)):
    with SessionLocal() as session:
        query = session.query(AlertEvent).order_by(AlertEvent.created_at.desc())
        total = query.count()
        rows = query.offset((page - 1) * page_size).limit(page_size).all()
        return {"items": [{"alert_id": row.alert_id, "rule_name": row.rule_name, "status": row.status, "severity": row.severity, "fingerprint": row.fingerprint, "observed_value": row.observed_value, "data_origin": row.data_origin, "details": row.details, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows], "page": page, "page_size": page_size, "total": total}