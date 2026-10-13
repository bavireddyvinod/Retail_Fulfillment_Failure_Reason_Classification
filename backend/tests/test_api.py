import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / f'retail-fulfillment-api-{os.getpid()}.sqlite3'}"

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


class InvestigationApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_investigation_persists_and_resumes_after_approval(self):
        original_threshold = settings.confidence_review_threshold
        settings.confidence_review_threshold = 0.9
        try:
            response = self.client.post("/api/investigations", json={
                "query": "ORD-10482 is delayed by a weather disruption at the carrier hub",
            })
            self.assertEqual(response.status_code, 201)
            pending = response.json()
            self.assertEqual(pending["status"], "awaiting_approval")
            self.assertEqual(pending["approval"]["status"], "pending")

            detail = self.client.get(f"/api/investigations/{pending['workflow_id']}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["classification"]["failure_reason"], "Carrier Delay")

            approved_response = self.client.post(
                f"/api/investigations/{pending['workflow_id']}/approval",
                json={
                    "decision": "approved",
                    "approved_action_ids": [pending["actions"][0]["action_id"]],
                    "comment": "Evidence verified",
                },
            )
            self.assertEqual(approved_response.status_code, 200)
            approved = approved_response.json()
            self.assertEqual(approved["status"], "completed")
            self.assertEqual(approved["approval"]["status"], "approved")
            self.assertEqual(approved["approval"]["reviewer_id"], "local-operator")
            executed = [action for action in approved["actions"] if action["status"] == "executed"]
            self.assertEqual(len(executed), 1)
            self.assertEqual(executed[0]["action_id"], pending["actions"][0]["action_id"])
            self.assertTrue(all(action["status"] == "proposed" for action in approved["actions"][1:]))
        finally:
            settings.confidence_review_threshold = original_threshold

    def test_request_trace_and_pagination_are_available_from_observability_api(self):
        response = self.client.post("/api/investigations", json={
            "query": "ORD-10482 is delayed by a weather disruption at the carrier hub",
        })
        self.assertEqual(response.status_code, 201)
        trace_id = response.headers["X-Trace-ID"]

        trace = self.client.get(f"/api/traces/{trace_id}")
        self.assertEqual(trace.status_code, 200)
        self.assertTrue(any(item["event_type"] == "span.ended" and item["agent_name"] == "classify" for item in trace.json()["spans"]))

        logs = self.client.get("/api/logs", params={"trace_id": trace_id, "data_origin": "DEMO", "page": 1, "page_size": 10})
        self.assertEqual(logs.status_code, 200)
        self.assertGreater(logs.json()["total"], 0)
        self.assertTrue(all(item["trace_id"] == trace_id for item in logs.json()["items"]))

        overview = self.client.get("/api/observability/overview")
        self.assertEqual(overview.status_code, 200)
        self.assertGreaterEqual(overview.json()["requests"]["total"], 2)
        infrastructure = overview.json()["infrastructure"]
        self.assertIn(infrastructure["status"], {"available", "unavailable"})
        if infrastructure["status"] == "available":
            self.assertEqual(infrastructure["scope"], "api_process")
            self.assertGreater(infrastructure["rss_bytes"], 0)

    def test_production_api_rejects_missing_credentials(self):
        with patch.object(settings, "app_env", "production"), patch.object(settings, "api_keys_json", ""):
            self.assertEqual(self.client.get("/api/dashboard").status_code, 401)

        with patch.object(settings, "app_env", "production"), patch.object(settings, "api_keys_json", '{"analyst-key":{"user_id":"ops-1","roles":["analyst"]}}'), patch("app.security.consume_rate_limit", return_value=1):
            self.assertEqual(self.client.get("/api/dashboard", headers={"Authorization": "Bearer analyst-key"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()