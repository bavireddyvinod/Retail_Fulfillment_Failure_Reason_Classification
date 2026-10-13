import json
import logging
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.config import settings

logger = logging.getLogger("retail_fulfillment")
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.propagate = False
_SENSITIVE_KEY = re.compile(r"(api.?key|authorization|password|secret|token|prompt|customer(_?name|_?id)?|email|phone|recipient|comment|message|raw_query)", re.IGNORECASE)


def _redact(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(child_key): _redact(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def emit_event(
    event_type: str,
    *,
    severity: str = "INFO",
    request_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    workflow_id: str | None = None,
    user_id: str | None = None,
    agent_name: str | None = None,
    tool_name: str | None = None,
    model_name: str | None = None,
    status: str | None = None,
    latency_ms: float | None = None,
    data_origin: str = "LIVE",
    details: dict[str, Any] | None = None,
) -> str:
    event_id = str(uuid4())
    sanitized = _redact(details or {})
    record = {
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.app_env,
        "service": "retail-fulfillment-api",
        "event_type": event_type,
        "severity": severity,
        "request_id": request_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "workflow_id": workflow_id,
        "user_id": user_id,
        "agent_name": agent_name,
        "tool_name": tool_name,
        "model_name": model_name,
        "status": status,
        "latency_ms": latency_ms,
        "data_origin": data_origin,
        "details": sanitized,
    }
    numeric_level = getattr(logging, severity.upper(), logging.INFO)
    logger.log(numeric_level, json.dumps(record, separators=(",", ":"), default=str))
    try:
        from app.database import SessionLocal
        from app.models import TelemetryEvent

        with SessionLocal.begin() as session:
            session.add(TelemetryEvent(**{key: value for key, value in record.items() if key != "timestamp"}))
    except Exception as error:
        logger.error(json.dumps({"event_type": "observability.persistence_failed", "source_event_type": event_type, "error_type": type(error).__name__}))
    return event_id


def record_guardrail_event(state: dict, decision: dict[str, Any]) -> str:
    severity = "WARNING" if decision.get("decision") in {"BLOCK", "HUMAN_REVIEW"} else "INFO"
    return emit_event(
        "guardrail.decision",
        severity=severity,
        request_id=state.get("request_id"),
        trace_id=state.get("trace_id"),
        workflow_id=state.get("workflow_id"),
        user_id=state.get("user_id"),
        status=decision.get("decision"),
        details=decision,
    )


def start_span(state: dict, name: str, kind: str, span_id: str) -> float:
    started = perf_counter()
    emit_event(
        "span.started",
        request_id=state.get("request_id"),
        trace_id=state.get("trace_id"),
        span_id=span_id,
        parent_span_id=state.get("root_span_id") or state.get("trace_id"),
        workflow_id=state.get("workflow_id"),
        user_id=state.get("user_id"),
        agent_name=name if kind == "agent" else None,
        tool_name=name if kind == "tool" else None,
        status="running",
        details={"span_name": name, "span_kind": kind},
    )
    return started


def end_span(state: dict, name: str, kind: str, span_id: str, started: float, status: str, details: dict | None = None) -> None:
    emit_event(
        "span.ended",
        severity="ERROR" if status == "error" else "INFO",
        request_id=state.get("request_id"),
        trace_id=state.get("trace_id"),
        span_id=span_id,
        parent_span_id=state.get("root_span_id") or state.get("trace_id"),
        workflow_id=state.get("workflow_id"),
        user_id=state.get("user_id"),
        agent_name=name if kind == "agent" else None,
        tool_name=name if kind == "tool" else None,
        status=status,
        latency_ms=round((perf_counter() - started) * 1000, 3),
        details={"span_name": name, "span_kind": kind, **(details or {})},
    )


def record_error(state: dict, source: str, error: Exception) -> None:
    emit_event(
        "application.error",
        severity="ERROR",
        request_id=state.get("request_id"),
        trace_id=state.get("trace_id"),
        workflow_id=state.get("workflow_id"),
        user_id=state.get("user_id"),
        status="error",
        details={"source": source, "error_type": type(error).__name__},
    )