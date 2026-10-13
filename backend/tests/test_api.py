import os
import tempfile
import unittest
from pathlib import Path

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
                json={"decision": "approved", "reviewer_id": "test-reviewer", "comment": "Evidence verified"},
            )
            self.assertEqual(approved_response.status_code, 200)
            approved = approved_response.json()
            self.assertEqual(approved["status"], "completed")
            self.assertEqual(approved["approval"]["status"], "approved")
            self.assertTrue(any(action["status"] == "created" for action in approved["actions"]))
        finally:
            settings.confidence_review_threshold = original_threshold


if __name__ == "__main__":
    unittest.main()