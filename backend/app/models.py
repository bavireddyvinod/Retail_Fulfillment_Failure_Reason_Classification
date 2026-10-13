from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Timestamped(Base):
    __abstract__ = True
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Timestamped):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="Operations User")
    role: Mapped[str] = mapped_column(String(80), default="analyst")


class Session(Timestamped):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300), default="Fulfillment investigation")


class Message(Timestamped):
    __tablename__ = "messages"
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    role: Mapped[str] = mapped_column(String(40))
    content: Mapped[str] = mapped_column(Text)


class Workflow(Timestamped):
    __tablename__ = "workflows"
    workflow_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[str] = mapped_column(String(80), index=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[dict] = mapped_column(JSON)


class AgentRun(Timestamped):
    __tablename__ = "agent_runs"
    workflow_id: Mapped[str] = mapped_column(String(80), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default="completed")
    details: Mapped[dict] = mapped_column(JSON)


class ToolCall(Timestamped):
    __tablename__ = "tool_calls"
    workflow_id: Mapped[str] = mapped_column(String(80), index=True)
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)


class FulfillmentFailure(Timestamped):
    __tablename__ = "fulfillment_failures"
    workflow_id: Mapped[str] = mapped_column(String(80), index=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    failure_reason: Mapped[str] = mapped_column(String(100))
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list] = mapped_column(JSON)


class InvestigationReport(Timestamped):
    __tablename__ = "investigation_reports"
    workflow_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    report: Mapped[dict] = mapped_column(JSON)


class Approval(Timestamped):
    __tablename__ = "approvals"
    workflow_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40))
    reviewer_id: Mapped[str] = mapped_column(String(80))
    comment: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Timestamped):
    __tablename__ = "audit_logs"
    workflow_id: Mapped[str] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    actor: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON)


class Document(Timestamped):
    __tablename__ = "documents"
    document_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[dict] = mapped_column(JSON)


class DocumentChunk(Timestamped):
    __tablename__ = "document_chunks"
    chunk_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    document_id: Mapped[str] = mapped_column(String(120), index=True)
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON)


class Evaluation(Timestamped):
    __tablename__ = "evaluations"
    workflow_id: Mapped[str] = mapped_column(String(80), index=True)
    evaluator: Mapped[str] = mapped_column(String(100))
    score: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON)


class TelemetryEvent(Timestamped):
    __tablename__ = "telemetry_events"
    event_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    environment: Mapped[str] = mapped_column(String(32), index=True)
    service: Mapped[str] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(80), index=True)
    span_id: Mapped[str | None] = mapped_column(String(80), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(80), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(80), index=True)
    user_id: Mapped[str | None] = mapped_column(String(80), index=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), index=True)
    tool_name: Mapped[str | None] = mapped_column(String(100), index=True)
    model_name: Mapped[str | None] = mapped_column(String(100), index=True)
    status: Mapped[str | None] = mapped_column(String(40), index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    data_origin: Mapped[str] = mapped_column(String(16), default="LIVE", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_telemetry_created_type", "created_at", "event_type"),
        Index("ix_telemetry_trace_created", "trace_id", "created_at"),
        Index("ix_telemetry_workflow_created", "workflow_id", "created_at"),
    )


class DriftObservation(Timestamped):
    __tablename__ = "drift_observations"
    observation_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    drift_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    reference_window: Mapped[str] = mapped_column(String(80))
    current_window: Mapped[str] = mapped_column(String(80))
    score: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    data_origin: Mapped[str] = mapped_column(String(16), default="LIVE", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class AlertRule(Timestamped):
    __tablename__ = "alert_rules"
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    operator: Mapped[str] = mapped_column(String(8))
    threshold: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16))
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=300)
    enabled: Mapped[bool] = mapped_column(default=True)


class AlertEvent(Timestamped):
    __tablename__ = "alert_events"
    alert_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    rule_name: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    fingerprint: Mapped[str] = mapped_column(String(160), index=True)
    observed_value: Mapped[float | None] = mapped_column(Float)
    data_origin: Mapped[str] = mapped_column(String(16), default="LIVE", index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)