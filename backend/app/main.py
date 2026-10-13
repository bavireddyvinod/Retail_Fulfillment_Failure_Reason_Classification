from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy import inspect

from app.cache import consume_rate_limit, get_dashboard, invalidate_dashboard, redis_health, set_dashboard
from app.alerting import evaluate_alerts, install_default_rules
from app.config import settings
from app.database import initialize_database, list_states, load_state, persist_state
from app.database import persist_evaluation
from app.evaluation import run_evaluation_suite
from app.guardrails import Principal, inspect_input, validate_approved_actions
from app.observability import emit_event, record_error
from app.observability_api import router as observability_router
from app.security import current_principal, require_production_auth_configuration, require_reviewer
from app.workflow import workflow


@asynccontextmanager
async def lifespan(_: FastAPI):
    require_production_auth_configuration()
    if settings.app_env.lower() in {"production", "prod"}:
        from app.database import engine

        if not inspect(engine).has_table("alembic_version"):
            raise RuntimeError("Run 'alembic upgrade head' before starting the production API")
    else:
        initialize_database()
    install_default_rules()
    yield


app = FastAPI(
    title="Retail Fulfillment Investigation API",
    description="Evidence-backed, multi-agent fulfillment failure investigations.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(observability_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Request-ID"],
)


@app.middleware("http")
async def correlate_requests(request: Request, call_next):
    provided_id = request.headers.get("x-request-id", "")
    request_id = provided_id if len(provided_id) <= 80 and provided_id.isprintable() else str(uuid4())
    trace_id = str(uuid4())
    root_span_id = str(uuid4())
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    request.state.root_span_id = root_span_id
    started = perf_counter()
    emit_event("span.started", request_id=request_id, trace_id=trace_id, span_id=root_span_id, status="running", details={"span_name": "http.request", "span_kind": "server"})
    response = None
    try:
        response = await call_next(request)
        return response
    except Exception as error:
        emit_event("request.failed", severity="ERROR", request_id=request_id, trace_id=trace_id, status="error", details={"path": request.url.path, "method": request.method, "error_type": type(error).__name__})
        raise
    finally:
        emit_event(
            "request.completed" if response else "request.failed",
            severity="ERROR" if response and response.status_code >= 500 else "INFO",
            request_id=request_id,
            trace_id=trace_id,
            status=str(response.status_code) if response else "error",
            latency_ms=round((perf_counter() - started) * 1000, 3),
            details={"path": request.url.path, "method": request.method},
        )
        emit_event(
            "span.ended",
            severity="ERROR" if response and response.status_code >= 500 else "INFO",
            request_id=request_id,
            trace_id=trace_id,
            span_id=root_span_id,
            status="completed" if response and response.status_code < 500 else "error",
            latency_ms=round((perf_counter() - started) * 1000, 3),
            details={"span_name": "http.request", "span_kind": "server", "http_status": response.status_code if response else None},
        )
        if response:
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id


def enforce_rate_limit(request: Request, principal: Principal) -> None:
    count = consume_rate_limit(f"rate:{principal.user_id}:{request.url.path}", settings.api_rate_limit_per_minute)
    if count is None:
        if settings.app_env.lower() in {"production", "prod"}:
            raise HTTPException(status_code=503, detail="Rate-limit service is unavailable")
        return
    if count == 0:
        emit_event("security.rate_limit", severity="WARNING", request_id=request.state.request_id, trace_id=request.state.trace_id, user_id=principal.user_id, status="blocked", details={"path": request.url.path})
        raise HTTPException(status_code=429, detail="Request rate limit exceeded")


class InvestigationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    order_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$")
    session_id: str | None = None


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    approved_action_ids: list[str] = Field(default_factory=list, max_length=10)
    comment: str = Field(default="", max_length=2000)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "retail-fulfillment-api",
        "openai_configured": bool(settings.openai_api_key),
        "redis": redis_health(),
    }


@app.get("/api/ready")
def readiness():
    from app.database import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database is not ready")


@app.get("/api/dashboard")
def dashboard(_: Principal = Depends(current_principal)):
    cached = get_dashboard()
    if cached:
        return cached
    states = list_states()
    reports = [state.get("final_response", {}) for state in states]
    result = {
        "metrics": {
            "investigations": len(states),
            "awaiting_approval": sum(state.get("status") == "awaiting_approval" for state in states),
            "high_confidence": sum(state.get("confidence", 0) >= 0.8 for state in states),
            "sla_risk": sum(report.get("classification", {}).get("failure_reason") == "SLA Breach" for report in reports),
        },
        "investigations": [state.get("final_response") or _summary_state(state) for state in states],
    }
    set_dashboard(result)
    return result


def _summary_state(state: dict) -> dict:
    classification = state.get("classification_result", {})
    return {
        "workflow_id": state.get("workflow_id"),
        "status": state.get("status", "investigating"),
        "order": state.get("fulfillment_data", {}).get("order", {"order_id": state.get("order_id")}),
        "summary": f"Investigation identified {classification.get('failure_reason', 'an operational exception')} as the most likely root cause.",
        "classification": classification,
        "confidence": state.get("confidence", 0),
        "approval": state.get("human_approval", {"required": False, "status": "not_required"}),
        "generated_at": state.get("created_at"),
    }


@app.post("/api/investigations", status_code=201)
def create_investigation(request: InvestigationRequest, http_request: Request, principal: Principal = Depends(current_principal)):
    enforce_rate_limit(http_request, principal)
    workflow_id = str(uuid4())
    input_decision = inspect_input(request.query, request.order_id)
    state = {
        "workflow_id": workflow_id,
        "request_id": http_request.state.request_id,
        "trace_id": http_request.state.trace_id,
        "root_span_id": http_request.state.root_span_id,
        "conversation_id": request.session_id or str(uuid4()),
        "root_span_id": str(uuid4()),
        "session_id": request.session_id or str(uuid4()),
        "user_id": principal.user_id,
        "user_query": request.query,
        "order_id": request.order_id,
        "conversation_history": [],
        "tool_results": [],
        "errors": [],
        "guardrail_decisions": [],
        "executed_actions": [],
        "approved_action_ids": [],
        "agent_trace": [],
        "retry_count": 0,
        "status": "investigating",
        "human_approval": {"required": False, "status": "not_required"},
    }
    try:
        if input_decision["decision"] == "BLOCK":
            input_decision.update({"guardrail": "investigation_input", "timestamp": datetime.now(timezone.utc).isoformat()})
            state["blocked"] = True
            state["status"] = "blocked"
            state["guardrail_decisions"].append(input_decision)
            state["errors"].append(input_decision["reason_code"])
        result = workflow.invoke(state)
        persist_state(result)
        invalidate_dashboard()
        return result.get("final_response", result)
    except Exception as error:
        record_error(state, "create_investigation", error)
        raise HTTPException(status_code=503, detail=f"Investigation could not be completed: {type(error).__name__}") from error


@app.get("/api/investigations/{workflow_id}")
def get_investigation(workflow_id: str, _: Principal = Depends(current_principal)):
    state = load_state(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return state.get("final_response") or _summary_state(state)


@app.post("/api/investigations/{workflow_id}/approval")
def decide_approval(workflow_id: str, request: ApprovalRequest, http_request: Request, principal: Principal = Depends(current_principal)):
    require_reviewer(principal)
    enforce_rate_limit(http_request, principal)
    state = load_state(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Investigation not found")
    if state.get("human_approval", {}).get("status") != "pending":
        raise HTTPException(status_code=409, detail="This investigation is not awaiting approval")
    if request.decision == "approved":
        try:
            validate_approved_actions(state.get("proposed_actions", []), request.approved_action_ids)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    elif request.approved_action_ids:
        raise HTTPException(status_code=422, detail="Rejected approvals cannot authorize actions")
    approval = state.get("human_approval", {})
    approval_id = approval.get("approval_id") or str(uuid4())
    responded_at = datetime.now(timezone.utc)
    try:
        requested_at = datetime.fromisoformat(approval["requested_at"].replace("Z", "+00:00"))
        approval_duration = max(0.0, (responded_at - requested_at).total_seconds())
    except (KeyError, ValueError, TypeError):
        requested_at = responded_at
        approval_duration = 0.0
    state["human_approval"] = {
        "required": True,
        "status": request.decision,
        "approval_id": approval_id,
        "request_id": state.get("request_id"),
        "trace_id": state.get("trace_id"),
        "reviewer_id": principal.user_id,
        "comment": request.comment,
        "approved_action_ids": request.approved_action_ids,
        "requested_at": requested_at.isoformat(),
        "responded_at": responded_at.isoformat(),
        "duration_seconds": approval_duration,
        "policy_version": "guardrails-v1",
    }
    state["approved_action_ids"] = request.approved_action_ids
    state["status"] = "investigating"
    try:
        result = workflow.invoke(state)
        persist_state(result)
        invalidate_dashboard()
        emit_event("approval.decided", request_id=state.get("request_id"), trace_id=state.get("trace_id"), workflow_id=workflow_id, user_id=principal.user_id, status=request.decision, latency_ms=approval_duration * 1000, details={"approval_id": approval_id, "approved_action_ids": request.approved_action_ids, "requested_at": requested_at.isoformat(), "responded_at": responded_at.isoformat(), "duration_seconds": approval_duration, "policy_version": "guardrails-v1", "comment": request.comment})
        return result.get("final_response", result)
    except Exception as error:
        record_error(state, "decide_approval", error)
        raise HTTPException(status_code=503, detail=f"Approval could not be processed: {type(error).__name__}") from error


@app.get("/api/evaluations/summary")
def evaluation_summary(_: Principal = Depends(current_principal)):
    states = list_states()
    return {
        "count": len(states),
        "mean_confidence": round(sum(state.get("confidence", 0) for state in states) / len(states), 3) if states else 0,
        "citation_coverage": round(sum(bool(state.get("citations")) for state in states) / len(states), 3) if states else 0,
        "validation_pass_rate": round(sum(state.get("validation_result", {}).get("decision") == "PASS" for state in states) / len(states), 3) if states else 0,
    }


@app.post("/api/evaluations/run")
def run_evaluations(request: Request, principal: Principal = Depends(current_principal)):
    enforce_rate_limit(request, principal)
    result = run_evaluation_suite()
    persist_evaluation(result["results"])
    return result


@app.post("/api/alerts/evaluate")
def evaluate_alert_rules(request: Request, principal: Principal = Depends(current_principal)):
    require_reviewer(principal)
    enforce_rate_limit(request, principal)
    return {"fired": evaluate_alerts(), "data_origin": "LIVE"}