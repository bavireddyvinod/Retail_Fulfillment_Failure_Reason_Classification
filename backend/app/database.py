import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)


def persist_state(state: dict) -> None:
    from app.models import AgentRun, Approval, AuditLog, FulfillmentFailure, InvestigationReport, ToolCall, Workflow

    workflow_id = state["workflow_id"]
    with SessionLocal.begin() as session:
        workflow_record = session.query(Workflow).filter_by(workflow_id=workflow_id).one_or_none()
        previous_state = workflow_record.state if workflow_record else {}
        values = {
            "session_id": state.get("session_id", ""),
            "user_id": state.get("user_id", "demo-analyst"),
            "order_id": state.get("order_id", ""),
            "status": state.get("status", "completed"),
            "state": state,
        }
        if workflow_record:
            for key, value in values.items():
                setattr(workflow_record, key, value)
        else:
            session.add(Workflow(workflow_id=workflow_id, **values))
        prior_run_ids = {entry.get("agent_run_id") for entry in previous_state.get("agent_trace", [])}
        for entry in state.get("agent_trace", []):
            if entry.get("agent_run_id") not in prior_run_ids:
                session.add(AgentRun(workflow_id=workflow_id, agent_name=entry["agent"], details=entry))
        prior_tool_calls = {
            json.dumps([entry.get("tool"), entry.get("arguments"), entry.get("result")], sort_keys=True, default=str)
            for entry in previous_state.get("tool_results", [])
        }
        for entry in state.get("tool_results", []):
            fingerprint = json.dumps([entry.get("tool"), entry.get("arguments"), entry.get("result")], sort_keys=True, default=str)
            if fingerprint not in prior_tool_calls:
                session.add(ToolCall(workflow_id=workflow_id, tool_name=entry["tool"], arguments=entry["arguments"], result=entry["result"]))
                prior_tool_calls.add(fingerprint)
        classification = state.get("classification_result", {})
        if classification:
            failure = session.query(FulfillmentFailure).filter_by(workflow_id=workflow_id).one_or_none()
            if failure is None:
                session.add(FulfillmentFailure(
                    workflow_id=workflow_id,
                    order_id=state.get("order_id", ""),
                    failure_reason=classification.get("failure_reason", "Unknown"),
                    confidence=classification.get("confidence", 0),
                    evidence=classification.get("evidence", []),
                ))
            else:
                failure.failure_reason = classification.get("failure_reason", "Unknown")
                failure.confidence = classification.get("confidence", 0)
                failure.evidence = classification.get("evidence", [])
        if state.get("final_response"):
            report = session.query(InvestigationReport).filter_by(workflow_id=workflow_id).one_or_none()
            if report:
                report.report = state["final_response"]
            else:
                session.add(InvestigationReport(workflow_id=workflow_id, report=state["final_response"]))
        approval = state.get("human_approval", {})
        if approval.get("status") in ["approved", "rejected"]:
            session.add(Approval(workflow_id=workflow_id, status=approval["status"], reviewer_id=approval.get("reviewer_id", "demo-analyst"), comment=approval.get("comment", "")))
        session.add(AuditLog(workflow_id=workflow_id, event_type="workflow.updated", actor=state.get("user_id", "demo-analyst"), details={"status": values["status"], "order_id": values["order_id"]}))


def load_state(workflow_id: str) -> dict | None:
    from app.models import Workflow

    with SessionLocal() as session:
        record = session.query(Workflow).filter_by(workflow_id=workflow_id).one_or_none()
        return record.state if record else None


def list_states(limit: int = 50) -> list[dict]:
    from app.models import Workflow

    with SessionLocal() as session:
        records = session.query(Workflow).order_by(Workflow.created_at.desc()).limit(limit).all()
        return [record.state for record in records]


def persist_evaluation(results: list[dict]) -> None:
    from app.models import Evaluation

    with SessionLocal.begin() as session:
        session.add_all([
            Evaluation(
                workflow_id=result["workflow_id"],
                evaluator="fulfillment-golden-v1",
                score=float(result["correct"]),
                details=result,
            )
            for result in results
        ])