from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.config import settings
from app.database import SessionLocal
from app.models import AlertEvent, AlertRule, TelemetryEvent
from app.observability import emit_event

DEFAULT_RULES = [
    ("request-error-rate", "request.error_rate", ">", 0.05, "CRITICAL"),
    ("agent-failure-rate", "agent.failure_rate", ">", 0.10, "WARNING"),
    ("tool-failure-rate", "tool.failure_rate", ">", 0.10, "WARNING"),
    ("rag-no-result-rate", "rag.no_result_rate", ">", 0.20, "WARNING"),
    ("llm-p95-latency", "llm.p95_latency_ms", ">", 5000, "WARNING"),
    ("daily-llm-budget", "llm.daily_cost_usd", ">", 10, "CRITICAL"),
    ("guardrail-block-rate", "guardrail.block_rate", ">", 0.75, "WARNING"),
]


def install_default_rules() -> None:
    with SessionLocal.begin() as session:
        existing = {row.name for row in session.query(AlertRule.name).all()}
        for name, metric, operator, threshold, severity in DEFAULT_RULES:
            if name not in existing:
                session.add(AlertRule(name=name, metric=metric, operator=operator, threshold=threshold, severity=severity, cooldown_seconds=300, enabled=True))


def _measure(session, metric: str, since: datetime) -> float | None:
    query = session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == "LIVE", TelemetryEvent.created_at >= since)
    if metric == "request.error_rate":
        events = query.filter(TelemetryEvent.event_type == "request.completed").all()
        return sum(event.status in {"error", "500", "502", "503", "504"} for event in events) / len(events) if events else None
    if metric in {"agent.failure_rate", "tool.failure_rate", "rag.no_result_rate", "guardrail.block_rate"}:
        event_type = {"agent.failure_rate": "span.ended", "tool.failure_rate": "tool.call", "rag.no_result_rate": "rag.retrieval", "guardrail.block_rate": "guardrail.decision"}[metric]
        events = query.filter(TelemetryEvent.event_type == event_type).all()
        if not events:
            return None
        failures = sum(event.status in {"error", "failed", "no_results", "BLOCK"} for event in events)
        return failures / len(events)
    if metric == "llm.p95_latency_ms":
        latencies = [row.latency_ms for row in query.filter(TelemetryEvent.event_type == "llm.call", TelemetryEvent.latency_ms.isnot(None)).order_by(TelemetryEvent.created_at.desc()).limit(5000).all()]
        latencies.sort()
        return latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None
    if metric == "llm.daily_cost_usd":
        cost = 0.0
        found = False
        for event in query.filter(TelemetryEvent.event_type == "llm.call").limit(5000).all():
            value = (event.details or {}).get("estimated_cost_usd")
            if isinstance(value, (int, float)):
                cost += float(value)
                found = True
        return cost if found else None
    return None


def evaluate_alerts(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    fired = []
    with SessionLocal.begin() as session:
        rules = session.query(AlertRule).filter_by(enabled=True).all()
        for rule in rules:
            observed = _measure(session, rule.metric, since)
            breached = observed is not None and (observed > rule.threshold if rule.operator == ">" else observed < rule.threshold)
            fingerprint = f"{rule.name}:LIVE:24h"
            prior = session.query(AlertEvent).filter_by(fingerprint=fingerprint).order_by(AlertEvent.created_at.desc()).first()
            if not breached:
                if prior and prior.status == "active":
                    prior.status = "resolved"
                    prior.details = {**(prior.details or {}), "resolved_at": now.isoformat(), "observed_value": observed}
                continue
            if prior and prior.status == "active":
                continue
            if prior and prior.created_at and prior.created_at.replace(tzinfo=timezone.utc) + timedelta(seconds=rule.cooldown_seconds) > now:
                continue
            details = {"metric": rule.metric, "operator": rule.operator, "threshold": rule.threshold, "window": "24h", "policy_version": "alerts-v1"}
            alert = AlertEvent(alert_id=str(uuid4()), rule_name=rule.name, status="active", severity=rule.severity, fingerprint=fingerprint, observed_value=observed, data_origin="LIVE", details=details)
            session.add(alert)
            fired.append({"alert_id": alert.alert_id, "rule_name": rule.name, "severity": rule.severity, "observed_value": observed, "threshold": rule.threshold})
    for alert in fired:
        emit_event("alert.triggered", severity=alert["severity"], status="active", details=alert)
    return fired


def cleanup_expired_observability_events(now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max(1, min(3650, settings.observability_retention_days)))
    with SessionLocal.begin() as session:
        return session.query(TelemetryEvent).filter(TelemetryEvent.data_origin == "LIVE", TelemetryEvent.created_at < cutoff).delete(synchronize_session=False)