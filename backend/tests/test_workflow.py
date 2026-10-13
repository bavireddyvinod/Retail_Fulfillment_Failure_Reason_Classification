import unittest

from app.config import settings
from app.evaluation import run_evaluation_suite
from app.rag import retrieve_policy_context
from app.workflow import workflow


class WorkflowTests(unittest.TestCase):
    def test_demo_investigation_returns_grounded_classification(self):
        result = workflow.invoke({
            "workflow_id": "test-workflow",
            "session_id": "test-session",
            "user_id": "test-analyst",
            "user_query": "ORD-10482 is late after a weather delay at the carrier hub",
            "tool_results": [],
            "errors": [],
            "agent_trace": [],
            "retry_count": 0,
            "human_approval": {"required": False, "status": "not_required"},
        })

        self.assertEqual(result["classification_result"]["failure_reason"], "Carrier Delay")
        self.assertGreaterEqual(result["confidence"], settings.confidence_review_threshold)
        self.assertTrue(result["citations"])
        self.assertEqual(result["validation_result"]["decision"], "PASS")
        self.assertTrue(result["final_response"]["evidence"])
        self.assertTrue(any(item["agent"] == "Policy & SOP RAG" for item in result["agent_trace"]))

    def test_empty_request_is_blocked_before_tools_run(self):
        result = workflow.invoke({
            "workflow_id": "blocked-workflow",
            "user_query": "   ",
            "tool_results": [],
            "errors": [],
            "agent_trace": [],
            "retry_count": 0,
        })

        self.assertTrue(result["blocked"])
        self.assertEqual(result["tool_results"], [])
        self.assertEqual(result["final_response"]["status"], "blocked")

    def test_uncertain_classification_waits_for_approval(self):
        original_threshold = settings.confidence_review_threshold
        settings.confidence_review_threshold = 0.9
        try:
            result = workflow.invoke({
                "workflow_id": "review-workflow",
                "user_query": "ORD-10482 has a carrier weather delay",
                "tool_results": [],
                "errors": [],
                "agent_trace": [],
                "retry_count": 0,
                "human_approval": {"required": False, "status": "not_required"},
            })
        finally:
            settings.confidence_review_threshold = original_threshold

        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["human_approval"]["status"], "pending")
        self.assertEqual(result.get("proposed_actions", []), [])

    def test_retrieved_policy_has_citable_metadata(self):
        context, citations = retrieve_policy_context("carrier weather delivery delay policy", "shipment")

        self.assertIn("weather disruption", context.lower())
        self.assertTrue(citations)
        self.assertTrue(all(citation.get("document_id") and citation.get("source") for citation in citations))

    def test_golden_evaluation_cases_cover_failure_categories(self):
        result = run_evaluation_suite()

        self.assertEqual(result["count"], 12)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["citation_coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()