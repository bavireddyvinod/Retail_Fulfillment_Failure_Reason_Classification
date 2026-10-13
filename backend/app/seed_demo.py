import argparse
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from app.database import SessionLocal, initialize_database
from app.models import AlertEvent, AlertRule, DriftObservation, TelemetryEvent


def seed_demo() -> int:
    initialize_database()
    now = datetime.now(timezone.utc)
    event_types = [
        ("request.completed", "INFO", "completed"),
        ("span.started", "INFO", "running"),
        ("span.ended", "INFO", "completed"),
        ("tool.call", "INFO", "success"),
        ("rag.retrieval", "INFO", "success"),
        ("llm.call", "INFO", "success"),
        ("guardrail.decision", "INFO", "PASS"),
        ("approval.decided", "INFO", "approved"),
        ("request.completed", "ERROR", "503"),
        ("application.error", "ERROR", "error"),
        ("system.metric", "INFO", "sampled"),
        ("database.metric", "INFO", "sampled"),
    ]
    with SessionLocal.begin() as session:
        existing = session.query(TelemetryEvent).filter_by(data_origin="DEMO", environment="demo").count()
        if existing:
            return existing
        for index in range(72):
            event_type, severity, status = event_types[index % len(event_types)]
            trace_number = index // 6
            trace_id = str(uuid5(NAMESPACE_URL, f"retail-fulfillment-demo-trace-{trace_number}"))
            workflow_id = str(uuid5(NAMESPACE_URL, f"retail-fulfillment-demo-workflow-{trace_number}"))
            agent_name = "Shipment Investigation" if index % len(event_types) in {1, 2} else None
            tool_name = "get_tracking" if event_type == "tool.call" else None
            model_name = "gpt-4o-mini" if event_type == "llm.call" else None
            span_id = str(uuid5(NAMESPACE_URL, f"retail-fulfillment-demo-span-{index // 2}")) if event_type.startswith("span.") else None
            details = {"sample": True, "index": index, "metric": "request_latency_ms", "value": 180 + (index % 8) * 45} if event_type.endswith("metric") else {"data_origin": "DEMO"}
            session.add(TelemetryEvent(
                event_id=str(uuid5(NAMESPACE_URL, f"retail-fulfillment-demo-event-{index}")),
                environment="demo",
                service="retail-fulfillment-api",
                event_type=event_type,
                severity=severity,
                request_id=str(uuid5(NAMESPACE_URL, f"retail-fulfillment-demo-request-{trace_number}")),
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=None,
                workflow_id=workflow_id,
                user_id="demo-operator",
                agent_name=agent_name,
                tool_name=tool_name,
                model_name=model_name,
                status=status,
                latency_ms=float(100 + index * 7) if event_type in {"request.completed", "span.ended", "tool.call", "llm.call", "rag.retrieval"} else None,
                data_origin="DEMO",
                details=details,
                created_at=now - timedelta(minutes=index * 17),
            ))
        session.add(DriftObservation(
            observation_id=str(uuid5(NAMESPACE_URL, "retail-fulfillment-demo-drift")),
            drift_type="prediction",
            status="WARNING",
            reference_window="DEMO reference: prior 14 days",
            current_window="DEMO current: last 14 days",
            score=0.14,
            threshold=0.1,
            data_origin="DEMO",
            details={"sample": True, "metric": "prediction_class_distribution"},
        ))
        session.add(AlertRule(name="demo-high-latency", metric="request.p95_latency_ms", operator=">", threshold=2500, severity="WARNING", cooldown_seconds=300, enabled=False))
        session.add(AlertEvent(
            alert_id=str(uuid5(NAMESPACE_URL, "retail-fulfillment-demo-alert")),
            rule_name="demo-high-latency",
            status="resolved",
            severity="WARNING",
            fingerprint="demo-high-latency:sample",
            observed_value=2600,
            data_origin="DEMO",
            details={"sample": True, "description": "Illustrative demo alert; not production telemetry."},
            created_at=now - timedelta(minutes=35),
        ))
    return 72


def remove_demo() -> dict[str, int]:
    initialize_database()
    with SessionLocal.begin() as session:
        removed = {
            "telemetry_events": session.query(TelemetryEvent).filter_by(data_origin="DEMO").delete(synchronize_session=False),
            "drift_observations": session.query(DriftObservation).filter_by(data_origin="DEMO").delete(synchronize_session=False),
            "alert_events": session.query(AlertEvent).filter_by(data_origin="DEMO").delete(synchronize_session=False),
            "alert_rules": session.query(AlertRule).filter_by(name="demo-high-latency").delete(synchronize_session=False),
        }
    return removed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed or remove explicitly marked observability demo data")
    parser.add_argument("--remove", action="store_true", help="Remove DEMO-origin telemetry, drift, alerts, and seed rule only")
    arguments = parser.parse_args()
    print(remove_demo() if arguments.remove else {"seeded_events": seed_demo(), "data_origin": "DEMO"})