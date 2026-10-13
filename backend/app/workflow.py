import json
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.config import settings
from app.guardrails import inspect_input, validate_approved_actions
from app.observability import emit_event, end_span, record_guardrail_event, record_error, start_span
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
    return [*state.get("agent_trace", []), {
        "agent_run_id": str(uuid4()),
        "agent": agent,
        "summary": summary,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": state.get("request_id"),
        "trace_id": state.get("trace_id"),
        "workflow_id": state.get("workflow_id"),
    }]


def input_guardrails(state: AgentState) -> dict[str, Any]:
    query = state.get("user_query", "").strip()
    decision_result = inspect_input(query, state.get("order_id"))
    decision_record = {
        **decision_result,
        "guardrail": "investigation_input",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": state.get("request_id"),
        "trace_id": state.get("trace_id"),
        "workflow_id": state.get("workflow_id"),
    }
    record_guardrail_event(state, decision_record)
    if decision_result["decision"] == "BLOCK":
        return {
            "blocked": True,
            "status": "blocked",
            "errors": [decision_result["reason_code"]],
            "guardrail_decisions": [*state.get("guardrail_decisions", []), decision_record],
            "agent_trace": _trace(state, "Input Guardrails", f"Blocked request: {decision_result['reason_code']}"),
        }
    return {
        "blocked": False,
        "guardrail_decisions": [*state.get("guardrail_decisions", []), decision_record],
        "agent_trace": _trace(state, "Input Guardrails", "Request passed input and scope validation"),
    }


def fulfillment_triage(state: AgentState) -> dict[str, Any]:
    query = state.get("user_query", "")
    order_match = re.search(r"\bORD[- ]?\d+\b", query, re.IGNORECASE)
    order_id = state.get("order_id") or (order_match.group(0).upper().replace(" ", "-") if order_match else "")
    lowered = query.lower()
    category = "shipment" if any(term in lowered for term in ["carrier", "delivery", "shipment", "tracking", "late"]) else "inventory" if any(term in lowered for term in ["stock", "inventory", "pick", "warehouse"]) else "fulfillment"
    priority = "high" if any(term in lowered for term in ["urgent", "critical", "customer impact", "sla", "lost"]) else "normal"
    triage = {
        "intent": "investigate_fulfillment_failure",
        "category": category,
        "priority": priority,
        "order_id": order_id,
        "fulfillment_center": next((match.group(0).upper() for match in [re.search(r"\bFC-[A-Za-z0-9-]+\b", query, re.IGNORECASE)] if match), None),
        "entities": {"order_id": order_id},
        "missing_information": [] if order_id else ["order_id"],
        "confidence": 0.91 if order_id else 0.35,
    }
    return {"order_id": order_id, "triage": triage, "status": "investigating" if order_id else "needs_information", "agent_trace": _trace(state, "Fulfillment Triage", f"Identified {category} incident for {order_id or 'an order identifier is missing'}")}


def route_after_triage(state: AgentState) -> str:
    return "supervisor" if state.get("order_id") else "response"


def supervisor(state: AgentState) -> dict[str, Any]:
    return {"agent_trace": _trace(state, "Supervisor", "Assigned order, inventory, shipment, and policy investigations")}


def order_retrieval(state: AgentState) -> dict[str, Any]:
    order_id = state.get("order_id", "ORD-10482")
    order, order_log = invoke_tool("Order Retrieval", get_order, {"order_id": order_id}, context=state)
    customer, customer_log = invoke_tool("Order Retrieval", get_customer, {"customer_id": order["customer_id"]}, context=state)
    events, events_log = invoke_tool("Order Retrieval", get_order_events, {"order_id": order_id}, context=state)
    return {
        "fulfillment_data": {"order": order, "customer": customer, "events": events},
        "tool_results": [*state.get("tool_results", []), order_log, customer_log, events_log],
        "agent_trace": _trace(state, "Order Retrieval", "Retrieved order, customer, payment, items, and timeline"),
    }


def inventory_investigation(state: AgentState) -> dict[str, Any]:
    order = state.get("fulfillment_data", {}).get("order", {})
    if not order.get("items") or not order["items"][0].get("sku") or not order.get("fulfillment_center"):
        return {"inventory_data": {}, "errors": [*state.get("errors", []), "inventory_lookup_missing_order_item_or_fulfillment_center"], "agent_trace": _trace(state, "Inventory Investigation", "Skipped inventory lookup because required order fields were absent")}
    args = {"sku": order["items"][0]["sku"], "fulfillment_center": order["fulfillment_center"]}
    inventory, inventory_log = invoke_tool("Inventory Investigation", get_inventory, args, context=state)
    movements, movements_log = invoke_tool("Inventory Investigation", get_inventory_movements, args, context=state)
    reservations, reservations_log = invoke_tool("Inventory Investigation", get_stock_reservations, args, context=state)
    return {
        "inventory_data": {"inventory": inventory, "movements": movements, "reservations": reservations},
        "tool_results": [*state.get("tool_results", []), inventory_log, movements_log, reservations_log],
        "agent_trace": _trace(state, "Inventory Investigation", "Checked stock, movements, and reservations"),
    }


def shipment_investigation(state: AgentState) -> dict[str, Any]:
    order_id = state.get("order_id", "ORD-10482")
    tracking, tracking_log = invoke_tool("Shipment Investigation", get_tracking, {"order_id": order_id}, context=state)
    updates, updates_log = invoke_tool("Shipment Investigation", get_carrier_updates, {"tracking_number": tracking["tracking_number"]}, context=state)
    exception, exception_log = invoke_tool("Shipment Investigation", get_delivery_exception, {"order_id": order_id}, context=state)
    return {
        "shipment_data": {"tracking": tracking, "carrier_updates": updates, "delivery_exception": exception},
        "tool_results": [*state.get("tool_results", []), tracking_log, updates_log, exception_log],
        "agent_trace": _trace(state, "Shipment Investigation", "Checked tracking, carrier scans, and delivery exceptions"),
    }


def policy_rag(state: AgentState) -> dict[str, Any]:
    query = f"{state.get('user_query', '')} {state.get('triage', {}).get('category', '')} fulfillment policy"
    retrieval_started = perf_counter()
    context, citations = retrieve_policy_context(query, state.get("triage", {}).get("category", ""))
    emit_event(
        "rag.retrieval",
        request_id=state.get("request_id"),
        trace_id=state.get("trace_id"),
        workflow_id=state.get("workflow_id"),
        user_id=state.get("user_id"),
        status="success" if citations else "no_results",
        latency_ms=round((perf_counter() - retrieval_started) * 1000, 3),
        details={"filter_category": state.get("triage", {}).get("category", ""), "top_k": 4, "documents_retrieved": len(citations), "document_ids": [item.get("document_id") for item in citations], "sources": [item.get("source") for item in citations], "citation_validation": "pending", "retriever": "hybrid_mmr"},
    )
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
            structured = model.with_structured_output(Classification, include_raw=True)
            prompt = (
                f"Classify this retail fulfillment failure using exactly one taxonomy value: {FAILURE_REASONS}. "
                "Use only the evidence and policy excerpts supplied. Cite facts in evidence; lower confidence when facts conflict.\n"
                f"Incident: {state.get('user_query', '')}\n"
                f"Operational evidence: {json.dumps([state.get('fulfillment_data'), state.get('inventory_data'), state.get('shipment_data')])}\n"
                f"Policy context: {state.get('retrieved_context', '')}"
            )
            llm_started = perf_counter()
            raw_response = structured.invoke(prompt)
            result = raw_response.get("parsed")
            message = raw_response.get("raw")
            usage = getattr(message, "usage_metadata", None) or {}
            emit_event(
                "llm.call",
                request_id=state.get("request_id"),
                trace_id=state.get("trace_id"),
                workflow_id=state.get("workflow_id"),
                user_id=state.get("user_id"),
                agent_name="Root Cause Classification",
                model_name=settings.openai_model,
                status="success" if result else "invalid_output",
                latency_ms=round((perf_counter() - llm_started) * 1000, 3),
                details={"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens"), "usage_available": bool(usage), "prompt_template_version": "classification-v1"},
            )
            if result is None:
                raise ValueError("Structured classification returned no parsed result")
            if result.failure_reason not in FAILURE_REASONS:
                raise ValueError("Model returned a failure reason outside the supported taxonomy")
        except Exception as error:
            result = _rule_classification(state)
            errors.append(f"OpenAI classification fell back to evidence rules: {type(error).__name__}")
            emit_event("llm.call", severity="ERROR", request_id=state.get("request_id"), trace_id=state.get("trace_id"), workflow_id=state.get("workflow_id"), agent_name="Root Cause Classification", model_name=settings.openai_model, status="error", details={"error_type": type(error).__name__})
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
    if state.get("human_approval", {}).get("status") == "rejected":
        return "response"
    return "recommend"


def route_after_recommendation(state: AgentState) -> str:
    approval_status = state.get("human_approval", {}).get("status")
    if approval_status == "rejected":
        return "response"
    if approval_status == "approved":
        return "execute"
    classification = state.get("classification_result", {})
    order = state.get("fulfillment_data", {}).get("order", {})
    requires_review = (
        classification.get("requires_human_review", True)
        or state.get("confidence", 0) < settings.confidence_review_threshold
        or order.get("order_total", 0) >= 500
        or classification.get("failure_reason") == "SLA Breach"
    )
    return "human" if requires_review else "validate"


def retry_retrieval(state: AgentState) -> dict[str, Any]:
    return {"retry_count": state.get("retry_count", 0) + 1, "agent_trace": _trace(state, "Supervisor", "Retrying evidence retrieval because operational evidence was incomplete")}


def human_approval(state: AgentState) -> dict[str, Any]:
    existing = state.get("human_approval", {})
    status = existing.get("status")
    if status not in ["approved", "rejected"]:
        status = "pending"
    approval_id = existing.get("approval_id") or str(uuid4())
    requested_at = existing.get("requested_at") or datetime.now(timezone.utc).isoformat()
    approval = {
        **existing,
        "status": status,
        "required": True,
        "approval_id": approval_id,
        "request_id": state.get("request_id"),
        "trace_id": state.get("trace_id"),
        "order_id": state.get("order_id"),
        "proposed_action_ids": [item.get("action_id") for item in state.get("proposed_actions", [])],
        "requested_at": requested_at,
        "policy_version": "guardrails-v1",
        "reason": "Low confidence or material business impact requires a human decision.",
    }
    if not existing.get("approval_id"):
        emit_event("approval.requested", request_id=state.get("request_id"), trace_id=state.get("trace_id"), workflow_id=state.get("workflow_id"), user_id=state.get("user_id"), status="pending", details={"approval_id": approval_id, "order_id": state.get("order_id"), "proposed_action_ids": approval["proposed_action_ids"], "policy_version": "guardrails-v1"})
    return {
        "human_approval": approval,
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
    order_id = state.get("order_id", "ORD-UNKNOWN")
    customer_id = state.get("fulfillment_data", {}).get("customer", {}).get("customer_id", "")
    actions = recommendation_by_reason.get(reason, ["Create an operations investigation ticket", "Review customer impact and next steps"])
    proposed = [{
        "action_id": "operations_ticket",
        "action": actions[0],
        "tool": "create_ticket",
        "arguments": {"order_id": order_id, "title": f"{reason} investigation", "details": "; ".join(actions)},
        "status": "proposed",
        "requires_approval": True,
    }]
    if reason in ["SLA Breach", "Lost Shipment"]:
        proposed.append({
            "action_id": "supply_chain_escalation",
            "action": actions[0],
            "tool": "create_escalation",
            "arguments": {"order_id": order_id, "reason": reason},
            "status": "proposed",
            "requires_approval": True,
        })
    if reason == "Carrier Delay":
        proposed.append({
            "action_id": "customer_update_request",
            "action": actions[1],
            "tool": "send_notification",
            "arguments": {"recipient": customer_id or "operations", "message": f"We are investigating a carrier delay for order {order_id} and will share the next delivery update."},
            "status": "proposed",
            "requires_approval": True,
        })
    return {
        "proposed_actions": proposed,
        "status": "completed",
        "agent_trace": _trace(state, "Action Recommendation", f"Prepared {len(proposed)} actions; no external changes executed"),
    }


def execute_approved_actions(state: AgentState) -> dict[str, Any]:
    approval = state.get("human_approval", {})
    if approval.get("status") != "approved":
        record_guardrail_event(state, {"guardrail": "write_authorization", "decision": "BLOCK", "reason_code": "missing_human_approval", "severity": "high", "request_id": state.get("request_id"), "trace_id": state.get("trace_id"), "workflow_id": state.get("workflow_id")})
        return {"errors": [*state.get("errors", []), "write_action_missing_approval"], "status": "blocked"}
    try:
        approved = validate_approved_actions(state.get("proposed_actions", []), state.get("approved_action_ids", []))
    except ValueError as error:
        decision_record = {
            "guardrail": "approved_action_scope",
            "decision": "BLOCK",
            "reason_code": str(error),
            "severity": "high",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": state.get("request_id"),
            "trace_id": state.get("trace_id"),
            "workflow_id": state.get("workflow_id"),
        }
        record_guardrail_event(state, decision_record)
        return {
            "guardrail_decisions": [*state.get("guardrail_decisions", []), decision_record],
            "errors": [*state.get("errors", []), str(error)],
            "status": "blocked",
            "agent_trace": _trace(state, "Action Guardrails", "Blocked action outside the recorded approval scope"),
        }
    tools_by_name = {
        "create_ticket": create_ticket,
        "create_escalation": create_escalation,
        "send_notification": send_notification,
        "generate_case_report": generate_case_report,
    }
    executed_ids = {item.get("action_id") for item in state.get("executed_actions", [])}
    executions = list(state.get("executed_actions", []))
    tool_results = list(state.get("tool_results", []))
    errors = list(state.get("errors", []))
    action_status = {item.get("action_id"): item for item in state.get("proposed_actions", [])}
    for item in approved:
        if item["action_id"] in executed_ids:
            continue
        tool = tools_by_name.get(item.get("tool"))
        if tool is None:
            errors.append(f"No approved executor is registered for {item.get('tool')}")
            continue
        try:
            result, log = invoke_tool("Action Execution", tool, item["arguments"], authorized_write=True, context=state)
            if result.get("status") not in {"created", "queued", "generated"}:
                raise RuntimeError("Tool did not confirm the write result")
            execution = {"action_id": item["action_id"], "action": item["action"], "status": "executed", "result": result}
            executions.append(execution)
            tool_results.append(log)
            action_status[item["action_id"]] = {**item, "status": "executed", "result": result}
        except Exception as error:
            errors.append(f"Action {item['action_id']} failed: {type(error).__name__}")
            action_status[item["action_id"]] = {**item, "status": "failed"}
    return {
        "executed_actions": executions,
        "proposed_actions": [action_status.get(item.get("action_id"), item) for item in state.get("proposed_actions", [])],
        "tool_results": tool_results,
        "errors": errors,
        "status": "completed" if not errors else "partial_failure",
        "agent_trace": _trace(state, "Action Execution", f"Executed {len(executions)} specifically approved actions"),
    }


def validation(state: AgentState) -> dict[str, Any]:
    classification = state.get("classification_result", {})
    issues = []
    confidence = classification.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        issues.append("Classification confidence is outside the permitted range")
    if not classification.get("failure_reason"):
        issues.append("Classification is missing a failure reason")
    if not classification.get("evidence"):
        issues.append("Classification is missing supporting evidence")
    if isinstance(confidence, (int, float)) and confidence >= 0.7 and not state.get("citations"):
        issues.append("No policy citations were retrieved for a high-confidence classification")
    invalid_citations = [citation for citation in state.get("citations", []) if not citation.get("document_id") or not citation.get("source")]
    if invalid_citations:
        issues.append("A policy citation is missing source metadata")
    context = state.get("retrieved_context", "")
    unsupported_citations = [citation for citation in state.get("citations", []) if citation.get("quote") and citation["quote"][:60] not in context]
    if unsupported_citations:
        issues.append("A citation excerpt does not match the retrieved policy context")
    blocked = "Classification confidence is outside the permitted range" in issues
    decision_value = "BLOCK" if blocked else "RETRY" if issues and state.get("retry_count", 0) < settings.max_agent_retries else "HUMAN_REVIEW" if issues else "PASS"
    result = {"decision": decision_value, "issues": issues, "timestamp": datetime.now(timezone.utc).isoformat()}
    decision_record = {
        **result,
        "guardrail": "output_validation",
        "reason_code": "output_validation_pass" if not issues else "output_validation_failed",
        "severity": "high" if decision_value == "BLOCK" else "medium" if issues else "info",
        "request_id": state.get("request_id"),
        "trace_id": state.get("trace_id"),
        "workflow_id": state.get("workflow_id"),
    }
    record_guardrail_event(state, decision_record)
    decisions = [*state.get("guardrail_decisions", []), decision_record]
    if decision_value == "BLOCK":
        return {"validation_result": result, "guardrail_decisions": decisions, "status": "blocked", "agent_trace": _trace(state, "Validation & Guardrails", "Blocked an invalid output")}
    if decision_value == "HUMAN_REVIEW":
        approval = {
            "required": True,
            "status": "pending",
            "approval_id": str(uuid4()),
            "request_id": state.get("request_id"),
            "trace_id": state.get("trace_id"),
            "order_id": state.get("order_id"),
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "policy_version": "guardrails-v1",
            "reason": "; ".join(issues),
        }
        emit_event("approval.requested", request_id=state.get("request_id"), trace_id=state.get("trace_id"), workflow_id=state.get("workflow_id"), user_id=state.get("user_id"), status="pending", details={"approval_id": approval["approval_id"], "order_id": state.get("order_id"), "reason": approval["reason"], "policy_version": "guardrails-v1"})
        return {"validation_result": result, "guardrail_decisions": decisions, "status": "awaiting_approval", "human_approval": approval, "agent_trace": _trace(state, "Validation & Guardrails", "Escalated validation issues for human review")}
    return {"validation_result": result, "guardrail_decisions": decisions, "agent_trace": _trace(state, "Validation & Guardrails", f"Validation {decision_value.lower()}: {len(issues)} issues")}


def response_generation(state: AgentState) -> dict[str, Any]:
    approval_status = state.get("human_approval", {}).get("status")
    status = "blocked" if state.get("validation_result", {}).get("decision") == "BLOCK" or state.get("blocked") else "rejected" if approval_status == "rejected" else "awaiting_approval" if approval_status == "pending" else "needs_information" if state.get("triage", {}).get("missing_information") else state.get("status", "completed")
    response = {
        "workflow_id": state.get("workflow_id"),
        "status": status,
        "order": state.get("fulfillment_data", {}).get("order", {"order_id": state.get("order_id")}),
        "summary": "The request was blocked by input guardrails." if state.get("blocked") else "Provide an order ID before operational records can be retrieved." if status == "needs_information" else f"Investigation identified {state.get('classification_result', {}).get('failure_reason', 'an operational exception')} as the most likely root cause.",
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
    if decision_value == "RETRY" and state.get("retry_count", 0) < settings.max_agent_retries:
        return "retry"
    return "response"


def build_workflow():
    graph = StateGraph(AgentState)
    nodes = {
        "input_guardrails": input_guardrails,
        "triage": fulfillment_triage,
        "supervisor": supervisor,
        "order_retrieval": order_retrieval,
        "inventory_investigation": inventory_investigation,
        "shipment_investigation": shipment_investigation,
        "policy_rag": policy_rag,
        "classify": classify_root_cause,
        "retry_retrieval": retry_retrieval,
        "retry_classification": retry_classification,
        "human_approval": human_approval,
        "recommend": action_recommendation,
        "execute_approved_actions": execute_approved_actions,
        "validation": validation,
        "response": response_generation,
    }
    for name, handler in nodes.items():
        graph.add_node(name, _instrument_node(name, handler))
    graph.add_edge(START, "input_guardrails")
    graph.add_conditional_edges("input_guardrails", lambda state: "response" if state.get("blocked") else "triage", {"response": "response", "triage": "triage"})
    graph.add_conditional_edges("triage", route_after_triage, {"supervisor": "supervisor", "response": "response"})
    graph.add_edge("supervisor", "order_retrieval")
    graph.add_edge("order_retrieval", "inventory_investigation")
    graph.add_edge("inventory_investigation", "shipment_investigation")
    graph.add_edge("shipment_investigation", "policy_rag")
    graph.add_edge("policy_rag", "classify")
    graph.add_conditional_edges("classify", route_after_classification, {"recommend": "recommend", "response": "response"})
    graph.add_edge("retry_retrieval", "order_retrieval")
    graph.add_edge("human_approval", "validation")
    graph.add_conditional_edges("recommend", route_after_recommendation, {"human": "human_approval", "execute": "execute_approved_actions", "validate": "validation", "response": "response"})
    graph.add_edge("execute_approved_actions", "validation")
    graph.add_conditional_edges("validation", route_after_validation, {"retry": "retry_classification", "response": "response"})
    graph.add_edge("retry_classification", "classify")
    graph.add_edge("response", END)
    return graph.compile()


def _instrument_node(name, handler):
    def run(state: AgentState) -> dict[str, Any]:
        span_id = str(uuid4())
        started = start_span(state, name, "agent", span_id)
        try:
            result = handler(state)
            end_span(state, name, "agent", span_id, started, "completed", {"agent_run_id": span_id, "step_count": 1})
            return result
        except Exception as error:
            end_span(state, name, "agent", span_id, started, "error", {"agent_run_id": span_id, "error_type": type(error).__name__})
            record_error(state, f"agent:{name}", error)
            raise

    return run


workflow = build_workflow()