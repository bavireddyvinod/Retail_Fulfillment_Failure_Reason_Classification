import unittest
from datetime import datetime, timezone
from uuid import uuid4

from app.alerting import evaluate_alerts, install_default_rules
from app.database import SessionLocal, initialize_database
from app.models import TelemetryEvent
from app.observability import _redact
from app.seed_demo import remove_demo, seed_demo


class ObservabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_database()
        install_default_rules()

    def test_sensitive_telemetry_keys_are_redacted(self):
        redacted = _redact({"api_key": "secret-value", "customer_name": "Private Name", "status": "success"})
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["customer_name"], "[REDACTED]")
        self.assertEqual(redacted["status"], "success")

    def test_demo_seed_is_idempotent_and_cleanup_is_origin_scoped(self):
        first_count = seed_demo()
        second_count = seed_demo()
        self.assertEqual(first_count, 72)
        self.assertEqual(second_count, 72)
        with SessionLocal.begin() as session:
            session.add(TelemetryEvent(
                event_id=str(uuid4()), environment="test", service="test", event_type="request.completed", severity="INFO",
                status="200", data_origin="LIVE", details={},
            ))
        removed = remove_demo()
        self.assertEqual(removed["telemetry_events"], 72)
        with SessionLocal() as session:
            self.assertGreaterEqual(session.query(TelemetryEvent).filter_by(data_origin="LIVE").count(), 1)

    def test_alert_evaluation_deduplicates_a_real_threshold_breach(self):
        created_at = datetime.now(timezone.utc)
        with SessionLocal.begin() as session:
            for index in range(20):
                session.add(TelemetryEvent(
                    event_id=str(uuid4()), environment="test", service="test", event_type="request.completed", severity="ERROR" if index < 4 else "INFO",
                    status="503" if index < 4 else "200", data_origin="LIVE", details={}, created_at=created_at,
                ))

        fired = evaluate_alerts(created_at)
        duplicate_run = evaluate_alerts(created_at)

        self.assertTrue(any(item["rule_name"] == "request-error-rate" for item in fired))
        self.assertFalse(any(item["rule_name"] == "request-error-rate" for item in duplicate_run))


if __name__ == "__main__":
    unittest.main()