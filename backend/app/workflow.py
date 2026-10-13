import json
import re
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.config import settings
from app.rag import retrieve_policy_context
from app.state import AgentState
from app.tools import (
    create_escalation,
    create_ticket,
    generate_case_report,
    get_carrier_updates,
    get_customer,
    get_delivery_exception,
    get_inventory,
    get_inventory_movements,
    get_order,
    get_order_events,
    get_stock_reservations,
    get_tracking,
    invoke_tool,
    send_notification,
    send_notification,
)

FAILURE_REASONS = [
    "Inventory Unavailable", "Inventory Allocation Error", "Picking Failure", "Packing Error",
    "Damaged Product", "Carrier Delay", "Lost Shipment", "Customer Cancellation", "Address Error",
    "Payment Failure", "Fraud Hold", "Supplier Delay", "SLA Breach", "Last Mile Failure",
    "System Failure", "Fulfillment Capacity Constraint", "Return to Origin", "Unknown",
]


class Classification(BaseModel):
    failure_reason: str = Field(description="A reason from the supported fulfillment failure taxonomy")
    root_cause_category: str
    evidence: list[str]
    confidence: float = Field(ge=0, le=1)
    requires_human_review: bool


def _trace(state: AgentState, agent: str, summary: str) -> list[dict[str, Any]]:
    return [*state.get("agent_trace", []), {"agent": agent, "summary": summary, "timestamp": datetime.now(timezone.utc).isoformat()}]


def input_guardrails(state: AgentState) -> dict[str, Any]:
    query = state.get("user_query", "").strip()
    if not query:
        return {"blocked": True, "status": "blocked", "errors": ["Describe the fulfillment issue to begin an investigation."], "agent_trace": _trace(state, "Input Guardrails", "Blocked empty investigation request")}
    if len(query) > 4000:
        return {"blocked": True, "status": "blocked", "errors": ["Investigation description must be 4,000 characters or fewer."], "agent_trace": _trace(state, "Input Guardrails", "Blocked oversized request")}
    return {"blocked": False, "agent_trace": _trace(state, "Input Guardrails", "Request passed input validation")}


def fulfillment_triage(state: AgentState) -> dict[str, Any]:
    query = state.get("user_query", "")
    order_match = re.search(r"\bORD[- ]?\d+\b", query, re.IGNORECASE)
    order_id = state.get("order_id") or (order_match.group(0).upper().replace(" ", "-") if order_match else "ORD-10482")
    lowered = query.lower()
    category = "shipment" if any(term in lowered for term in ["carrier", "delivery", "shipment", "tracking", "late"]) else "inventory" if any(term in lowered for term in ["stock", "inventory", "pick", "warehouse"]) else "fulfillment"
    priority = "high" if any(term in lowered for term in ["urgent", "critical", "customer impact", "sla", "lost"]) else "normal"
    triage = {
        "intent": "investigate_fulfillment_failure",
        "category": category,
        "priority": priority,
        "order_id": order_id,
        "fulfillment_center": "FC-West-04",
        "entities": {"order_id": order_id},
        "missing_information": [],
        "confidence": 0.91 if order_match or state.get("order_id") else 0.68,
    }
    return {"order_id": order_id, "triage": triage, "agent_trace": _trace(state, "Fulfillment Triage", f"Identified {category} incident for {order_id}")}


def supervisor(state: AgentState) -> dict[str, Any]:
    return {"agent_trace": _trace(state, "Supervisor", "Assigned order, inventory, shipment, and policy investigations")}


def order_retrieval(state: AgentState) -> dict[str, Any]:
    order_id = state.get("order_id", "ORD-10482")
    order, order_log = invoke_tool("Order Retrieval", get_order, {"order_id": order_id})
    customer, customer_log = invoke_tool("Order Retrieval", get_customer, {"customer_id": order["customer_id"]})
    events, events_log = invoke_tool("Order Retrieval", get_order_events, {"order_id": order_id})
    return {
        "fulfillment_data": {"order": order, "customer": customer, "events": events},
        "tool_results": [*state.get("tool_results", []), order_log, customer_log, events_log],
        "agent_trace": _trace(state, "Order Retrieval", "Retrieved order, customer, payment, items, and timeline"),
    }


def inventory_investigation(state: AgentState) -> dict[str, Any]:
    order = state.get("fulfillment_data", {}).get("order", {})
    args = {"sku": order.get("items", [{}])[0].get("sku", "HME-4402"), "fulfillment_center": order.get("fulfillment_center", "FC-West-04")}
    inventory, inventory_log = invoke_tool("Inventory Investigation", get_inventory, args)
    movements, movements_log = invoke_tool("Inventory Investigation", get_inventory_movements, args)
    reservations, reservations_log = invoke_tool("Inventory Investigation", get_stock_reservations, args)
    return {
        "inventory_data": {"inventory": inventory, "movements": movements, "reservations": reservations},
        "tool_results": [*state.get("tool_results", []), inventory_log, movements_log, reservations_log],
        "agent_trace": _trace(state, "Inventory Investigation", "Checked stock, movements, and reservations"),
    }


def shipment_investigation(state: AgentState) -> dict[str, Any]:
    order_id = state.get("order_id", "ORD-10482")
    tracking, tracking_log = invoke_tool("Shipment Investigation", get_tracking, {"order_id": order_id})
    updates, updates_log = invoke_tool("Shipment Investigation", get_carrier_updates, {"tracking_number": tracking["tracking_number"]})
    exception, exception_log = invoke_tool("Shipment Investigation", get_delivery_exception, {"order_id": order_id})
    return {
        "shipment_data": {"tracking": tracking, "carrier_updates": updates, "delivery_exception": exception},
        "tool_results": [*state.get("tool_results", []), tracking_log, updates_log, exception_log],
        "agent_trace": _trace(state, "Shipment Investigation", "Checked tracking, carrier scans, and delivery exceptions"),
    }


def policy_rag(state: AgentState) -> dict[str, Any]:
    query = f"{state.get('user_query', '')} {state.get('triage', {}).get('category', '')} fulfillment policy"
    context, citations = retrieve_policy_context(query, state.get("triage", {}).get("category", ""))
    return {
        "retrieved_context": context,
        "rag_documents": citations,
        "citations": citations,
        "agent_trace": _trace(state, "Policy & SOP RAG", f"Retrieved {len(citations)} cited policy passages"),
    }


def _rule_classification(state: AgentState) -> Classification:
    all_text = " ".join([
        state.get("user_query", ""),
        json.dumps(state.get("fulfillment_data", {})),
        json.dumps(state.get("inventory_data", {})),
        json.dumps(state.get("shipment_data", {})),
    ]).lower()
    rules = [
        (["fraud", "fraud hold"], "Fraud Hold", "payment_risk"),
        (["payment failure", "payment declined", "payment was declined", "declined payment", "payment_status: declined"], "Payment Failure", "payment"),
        (["address", "invalid address"], "Address Error", "address_validation"),
        (["cancel", "cancellation"], "Customer Cancellation", "customer_request"),
        (["lost shipment", "shipment lost", "lost package"], "Lost Shipment", "carrier"),
        (["return to origin", "returned to sender", "rto"], "Return to Origin", "carrier"),
        (["sla breach", "missed sla", "service level breach"], "SLA Breach", "service_level"),
        (["last mile", "last-mile", "failed delivery", "delivery exception"], "Last Mile Failure", "last_mile"),
        (["weather", "carrier delay", "delayed", "delivery delay", "late delivery"], "Carrier Delay", "carrier"),
        (["damaged", "damage"], "Damaged Product", "warehouse_or_transit"),
        (["packing", "packaging"], "Packing Error", "warehouse"),
        (["pick failure", "picking", "pick error"], "Picking Failure", "warehouse"),
        (["supplier", "replenishment"], "Supplier Delay", "supplier"),
        (["system", "integration", "api error"], "System Failure", "technology"),
        (["fulfillment capacity", "capacity constraint", "capacity limit"], "Fulfillment Capacity Constraint", "capacity"),
        (["inventory allocation", "misallocated"], "Inventory Allocation Error", "inventory"),
        (["out of stock", "inventory unavailable"], "Inventory Unavailable", "inventory"),
    ]
    for signals, reason, category in rules:
        if any(signal in all_text for signal in signals):
            confidence = 0.84 if reason == "Carrier Delay" and "weather disruption" in all_text else 0.79
            break
    else:
        reason, category, confidence = "Unknown", "unknown", 0.42
    evidence = []
    tracking = state.get("shipment_data", {}).get("tracking", {})
    updates = state.get("shipment_data", {}).get("carrier_updates", [])
    if tracking:
        evidence.append(f"Carrier tracking reports '{tracking.get('status')}' at {tracking.get('last_scan')} ({tracking.get('last_scan_at')}).")
    if updates:
        evidence.append(f"Carrier update {updates[0].get('code')} states: {updates[0].get('description')}.")
    inventory = state.get("inventory_data", {}).get("inventory", {})
    if inventory:
        evidence.append(f"Inventory check found {inventory.get('available')} available units at {inventory.get('fulfillment_center')}.")
    if not evidence:
        evidence.append("The supplied incident description was used; no operational evidence was returned.")
    return Classification(
        failure_reason=reason,
        root_cause_category=category,
        evidence=evidence,
        confidence=confidence,
        requires_human_review=confidence < settings.confidence_review_threshold,
    )


def classify_root_cause(state: AgentState) -> dict[str, Any]:
    result: Classification
    if settings.openai_api_key:
        errors = []
        try:
            from langchain_openai import ChatOpenAI
            model = ChatOpenAI(model=settings.openai_model, temperature=0, api_key=settings.openai_api_key)
            structured = model.with_structured_output(Classification)
            prompt = (
                f"Classify this retail fulfillment failure using exactly one taxonomy value: {FAILURE_REASONS}. "
                "Use only the evidence and policy excerpts supplied. Cite facts in evidence; lower confidence when facts conflict.\n"
                f"Incident: {state.get('user_query', '')}\n"
                f"Operational evidence: {json.dumps([state.get('fulfillment_data'), state.get('inventory_data'), state.get('shipment_data')])}\n"
                f"Policy context: {state.get('retrieved_context', '')}"
            )
            result = structured.invoke(prompt)
            if result.failure_reason not in FAILURE_REASONS:
                raise ValueError("Model returned a failure reason outside the supported taxonomy")
        except Exception as error:
            result = _rule_classification(state)
            errors.append(f"OpenAI classification fell back to evidence rules: {type(error).__name__}")
    else:
        errors = []
        result = _rule_classification(state)
    if result.confidence < settings.confidence_review_threshold:
        result.requires_human_review = True
    classification = result.model_dump()
    return {
        "classification_result": classification,
        "investigation_result": classification,
        "confidence": result.confidence,
        "errors": [*state.get("errors", []), *errors],
        "agent_trace": _trace(state, "Root Cause Classification", f"Classified as {result.failure_reason} at {result.confidence:.0%} confidence"),
    }


def decision(state: AgentState) -> str:
    if state.get("blocked"):
        return "response"
    if state.get("retry_count", 0) < 1 and not state.get("tool_results"):
        return "retrieve"
    approval_status = state.get("human_approval", {}).get("status")
    if approval_status == "rejected":
        return "response"
    if approval_status == "approved":
        return "recommend"
    classification = state.get("classification_result", {})
    order = state.get("fulfillment_data", {}).get("order", {})
    requires_review = (
        classification.get("requires_human_review", True)
        or state.get("confidence", 0) < settings.confidence_review_threshold
        or order.get("order_total", 0) >= 500
        or classification.get("failure_reason") == "SLA Breach"
    )
    return "human" if requires_review else "recommend"


def retry_retrieval(state: AgentState) -> dict[str, Any]:
    return {"retry_count": state.get("retry_count", 0) + 1, "agent_trace": _trace(state, "Supervisor", "Retrying evidence retrieval because operational evidence was incomplete")}


def human_approval(state: AgentState) -> dict[str, Any]:
    existing = state.get("human_approval", {})
    status = existing.get("status")
    if status not in ["approved", "rejected"]:
        status = "pending"
    return {
        "human_approval": {**existing, "status": status, "required": True, "reason": "Low confidence or material business impact requires a human decision."},
        "status": "awaiting_approval" if status == "pending" else state.get("status", "investigating"),
        "agent_trace": _trace(state, "Human Approval", "Held action execution for human review"),
    }


def retry_classification(state: AgentState) -> dict[str, Any]:
    return {"retry_count": state.get("retry_count", 0) + 1, "agent_trace": _trace(state, "Supervisor", "Retrying classification after a validation issue")}


def action_recommendation(state: AgentState) -> dict[str, Any]:
    classification = state.get("classification_result", {})
    reason = classification.get("failure_reason", "Unknown")
    recommendation_by_reason = {
        "Carrier Delay": ["Contact carrier for an updated delivery commitment", "Proactively notify the customer"],
        "Lost Shipment": ["Open a carrier trace", "Prepare a replacement or refund decision"],
        "Inventory Unavailable": ["Check alternate fulfillment centers", "Reallocate inventory if available"],
        "Inventory Allocation Error": ["Reconcile stock reservations", "Reallocate inventory"],
        "Picking Failure": ["Open a warehouse pick investigation", "Verify remaining stock"],
        "Damaged Product": ["Inspect transit and packing records", "Prepare a replacement decision"],
        "SLA Breach": ["Escalate to the service-level owner", "Review customer recovery options"],
        "Last Mile Failure": ["Contact the last-mile carrier", "Verify the delivery exception and customer availability"],
        "Return to Origin": ["Confirm the carrier return scan", "Review address correction and reship options"],
        "Fulfillment Capacity Constraint": ["Check alternate fulfillment centers", "Review capacity allocation and promise dates"],
    }
    actions = recommendation_by_reason.get(reason, ["Create an operations investigation ticket", "Review customer impact and next steps"])
    order_id = state.get("order_id", "ORD-UNKNOWN")
    executed = []
    ticket, ticket_log = invoke_tool("Action Recommendation", create_ticket, {"order_id": order_id, "title": f"{reason} investigation", "details": "; ".join(actions)})
    executed.append(ticket)
    logs = [ticket_log]
    if reason in ["SLA Breach", "Lost Shipment"]:
        escalation, escalation_log = invoke_tool("Action Recommendation", create_escalation, {"order_id": order_id, "reason": reason})
        executed.append(escalation)
        logs.append(escalation_log)
    if reason == "Carrier Delay":
        customer_id = state.get("fulfillment_data", {}).get("customer", {}).get("customer_id", "operations")
        notification, notification_log = invoke_tool(
            "Action Recommendation",
            send_notification,
            {"recipient": customer_id, "message": f"We are investigating a carrier delay for order {order_id} and will share the next delivery update."},
        )
        executed.append(notification)
        logs.append(notification_log)
    report, report_log = invoke_tool("Action Recommendation", generate_case_report, {"order_id": order_id, "summary": f"{reason}: {'; '.join(classification.get('evidence', []))}"})
    executed.append(report)
    logs.append(report_log)
    return {
        "proposed_actions": [{"action": action, "status": "recommended"} for action in actions] + [{"action": item.get("title", item.get("reason", "Audit report")), "status": item.get("status", "created"), "reference": item.get("ticket_id", item.get("escalation_id", item.get("report_id")))} for item in executed],
        "tool_results": [*state.get("tool_results", []), *logs],
        "status": "completed",
        "agent_trace": _trace(state, "Action Recommendation", f"Created {len(executed)} audit and operations records"),
    }


def validation(state: AgentState) -> dict[str, Any]:
    classification = state.get("classification_result", {})
    issues = []
    if not classification.get("failure_reason"):
        issues.append("Classification is missing a failure reason")
    if not classification.get("evidence"):
        issues.append("Classification is missing supporting evidence")
    if classification.get("confidence", 0) >= 0.7 and not state.get("citations"):
        issues.append("No policy citations were retrieved for a high-confidence classification")
    invalid_citations = [citation for citation in state.get("citations", []) if not citation.get("document_id") or not citation.get("source")]
    if invalid_citations:
        issues.append("A policy citation is missing source metadata")
    context = state.get("retrieved_context", "")
    unsupported_citations = [citation for citation in state.get("citations", []) if citation.get("quote") and citation["quote"][:60] not in context]
    if unsupported_citations:
        issues.append("A citation excerpt does not match the retrieved policy context")
    result = {"decision": "RETRY" if issues and state.get("retry_count", 0) < 1 else "HUMAN_REVIEW" if issues else "PASS", "issues": issues}
    if result["decision"] == "HUMAN_REVIEW":
        return {"validation_result": result, "status": "awaiting_approval", "human_approval": {"required": True, "status": "pending", "reason": "; ".join(issues)}, "agent_trace": _trace(state, "Validation & Guardrails", "Escalated validation issues for human review")}
    return {"validation_result": result, "agent_trace": _trace(state, "Validation & Guardrails", f"Validation {result['decision'].lower()}: {len(issues)} issues")}


def response_generation(state: AgentState) -> dict[str, Any]:
    approval_status = state.get("human_approval", {}).get("status")
    status = "rejected" if approval_status == "rejected" else "awaiting_approval" if approval_status == "pending" else state.get("status", "completed")
    response = {
        "workflow_id": state.get("workflow_id"),
        "status": status,
        "order": state.get("fulfillment_data", {}).get("order", {"order_id": state.get("order_id")}),
        "summary": "The request was blocked by input guardrails." if state.get("blocked") else f"Investigation identified {state.get('classification_result', {}).get('failure_reason', 'an operational exception')} as the most likely root cause.",
        "classification": state.get("classification_result", {}),
        "evidence": state.get("classification_result", {}).get("evidence", []),
        "confidence": state.get("confidence", 0),
        "actions": state.get("proposed_actions", []),
        "approval": state.get("human_approval", {"required": False, "status": "not_required"}),
        "citations": state.get("citations", []),
        "validation": state.get("validation_result", {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "status": status,
        "final_response": response,
        "agent_trace": _trace(state, "Response Generation", "Prepared the evidence-backed investigation report"),
    }


def route_after_classification(state: AgentState) -> str:
    if state.get("blocked"):
        return "response"
    return decision(state)


def route_after_validation(state: AgentState) -> str:
    decision_value = state.get("validation_result", {}).get("decision")
    if decision_value == "RETRY" and state.get("retry_count", 0) < 1:
        return "retry"
    return "response"


def build_workflow():
    graph = StateGraph(AgentState)
    graph.add_node("input_guardrails", input_guardrails)
    graph.add_node("triage", fulfillment_triage)
    graph.add_node("supervisor", supervisor)
    graph.add_node("order_retrieval", order_retrieval)
    graph.add_node("inventory_investigation", inventory_investigation)
    graph.add_node("shipment_investigation", shipment_investigation)
    graph.add_node("policy_rag", policy_rag)
    graph.add_node("classify", classify_root_cause)
    graph.add_node("retry_retrieval", retry_retrieval)
    graph.add_node("retry_classification", retry_classification)
    graph.add_node("human_approval", human_approval)
    graph.add_node("recommend", action_recommendation)
    graph.add_node("validation", validation)
    graph.add_node("response", response_generation)
    graph.add_edge(START, "input_guardrails")
    graph.add_conditional_edges("input_guardrails", lambda state: "response" if state.get("blocked") else "triage", {"response": "response", "triage": "triage"})
    graph.add_edge("triage", "supervisor")
    graph.add_edge("supervisor", "order_retrieval")
    graph.add_edge("order_retrieval", "inventory_investigation")
    graph.add_edge("inventory_investigation", "shipment_investigation")
    graph.add_edge("shipment_investigation", "policy_rag")
    graph.add_edge("policy_rag", "classify")
    graph.add_conditional_edges("classify", route_after_classification, {"retrieve": "retry_retrieval", "human": "human_approval", "recommend": "recommend", "response": "response"})
    graph.add_edge("retry_retrieval", "order_retrieval")
    graph.add_edge("human_approval", "validation")
    graph.add_edge("recommend", "validation")
    graph.add_conditional_edges("validation", route_after_validation, {"retry": "retry_classification", "response": "response"})
    graph.add_edge("retry_classification", "classify")
    graph.add_edge("response", END)
    return graph.compile()


workflow = build_workflow()