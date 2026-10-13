from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.cache import get_dashboard, invalidate_dashboard, redis_health, set_dashboard
from app.config import settings
from app.database import initialize_database, list_states, load_state, persist_state
from app.database import persist_evaluation
from app.evaluation import run_evaluation_suite
from app.workflow import workflow


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="Retail Fulfillment Investigation API",
    description="Evidence-backed, multi-agent fulfillment failure investigations.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


class InvestigationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    order_id: str | None = None
    session_id: str | None = None
    user_id: str = "demo-analyst"


class ApprovalRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reviewer_id: str = "demo-analyst"
    comment: str = Field(default="", max_length=2000)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "retail-fulfillment-api",
        "openai_configured": bool(settings.openai_api_key),
        "redis": redis_health(),
    }


@app.get("/api/dashboard")
def dashboard():
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
def create_investigation(request: InvestigationRequest):
    workflow_id = str(uuid4())
    state = {
        "workflow_id": workflow_id,
        "session_id": request.session_id or str(uuid4()),
        "user_id": request.user_id,
        "user_query": request.query,
        "order_id": request.order_id,
        "conversation_history": [],
        "tool_results": [],
        "errors": [],
        "agent_trace": [],
        "retry_count": 0,
        "status": "investigating",
        "human_approval": {"required": False, "status": "not_required"},
    }
    try:
        result = workflow.invoke(state)
        persist_state(result)
        invalidate_dashboard()
        return result.get("final_response", result)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Investigation could not be completed: {type(error).__name__}") from error


@app.get("/api/investigations/{workflow_id}")
def get_investigation(workflow_id: str):
    state = load_state(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return state.get("final_response") or _summary_state(state)


@app.post("/api/investigations/{workflow_id}/approval")
def decide_approval(workflow_id: str, request: ApprovalRequest):
    state = load_state(workflow_id)
    if not state:
        raise HTTPException(status_code=404, detail="Investigation not found")
    if state.get("human_approval", {}).get("status") != "pending":
        raise HTTPException(status_code=409, detail="This investigation is not awaiting approval")
    state["human_approval"] = {
        "required": True,
        "status": request.decision,
        "reviewer_id": request.reviewer_id,
        "comment": request.comment,
    }
    state["status"] = "investigating"
    try:
        result = workflow.invoke(state)
        persist_state(result)
        invalidate_dashboard()
        return result.get("final_response", result)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Approval could not be processed: {type(error).__name__}") from error


@app.get("/api/evaluations/summary")
def evaluation_summary():
    states = list_states()
    return {
        "count": len(states),
        "mean_confidence": round(sum(state.get("confidence", 0) for state in states) / len(states), 3) if states else 0,
        "citation_coverage": round(sum(bool(state.get("citations")) for state in states) / len(states), 3) if states else 0,
        "validation_pass_rate": round(sum(state.get("validation_result", {}).get("decision") == "PASS" for state in states) / len(states), 3) if states else 0,
    }


@app.post("/api/evaluations/run")
def run_evaluations():
    result = run_evaluation_suite()
    persist_evaluation(result["results"])
    return result