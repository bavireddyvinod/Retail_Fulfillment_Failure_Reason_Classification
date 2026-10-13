from uuid import uuid4

from app.workflow import workflow

GOLDEN_CASES = [
    {"query": "ORD-10482 is late after a weather delay at the carrier hub", "order_id": "ORD-10482", "expected": "Carrier Delay"},
    {"query": "ORD-10999 is out of stock at the fulfillment center", "order_id": "ORD-10999", "expected": "Inventory Unavailable"},
    {"query": "ORD-10998 has been confirmed as a lost shipment", "order_id": "ORD-10998", "expected": "Lost Shipment"},
    {"query": "ORD-10997 has an invalid delivery address", "order_id": "ORD-10997", "expected": "Address Error"},
    {"query": "ORD-10996 supplier replenishment missed its committed date", "order_id": "ORD-10996", "expected": "Supplier Delay"},
]


def run_evaluation_suite() -> dict:
    results = []
    for sample in GOLDEN_CASES:
        workflow_id = str(uuid4())
        state = workflow.invoke({
            "workflow_id": workflow_id,
            "session_id": f"eval-{workflow_id}",
            "user_id": "evaluation-runner",
            "user_query": sample["query"],
            "order_id": sample["order_id"],
            "tool_results": [],
            "errors": [],
            "agent_trace": [],
            "retry_count": 0,
            "human_approval": {"required": False, "status": "not_required"},
        })
        classification = state.get("classification_result", {})
        passed = classification.get("failure_reason") == sample["expected"]
        results.append({
            "workflow_id": workflow_id,
            "expected": sample["expected"],
            "actual": classification.get("failure_reason", "Unknown"),
            "correct": passed,
            "confidence": state.get("confidence", 0),
            "citation_coverage": bool(state.get("citations")),
            "validation": state.get("validation_result", {}).get("decision", "BLOCK"),
        })
    count = len(results)
    return {
        "suite": "fulfillment-golden-v1",
        "count": count,
        "accuracy": round(sum(result["correct"] for result in results) / count, 3) if count else 0,
        "mean_confidence": round(sum(result["confidence"] for result in results) / count, 3) if count else 0,
        "citation_coverage": round(sum(result["citation_coverage"] for result in results) / count, 3) if count else 0,
        "results": results,
    }