import json
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from app.guardrails import validate_tool_invocation
from app.observability import emit_event, end_span, record_error, start_span


@tool
def get_order(order_id: str) -> str:
    """Retrieve order, item, payment, and fulfillment details for an order ID."""
    item_sku = "SKU-OOS" if order_id.endswith("999") else "HME-4402"
    item_name = "Outdoor Lantern" if item_sku == "SKU-OOS" else "Modular Storage Shelf"
    return json.dumps({
        "order_id": order_id,
        "customer_id": "CUS-2048",
        "customer_name": "Jordan Lee",
        "items": [{"sku": item_sku, "name": item_name, "quantity": 1}],
        "payment_status": "approved",
        "fulfillment_status": "exception",
        "fulfillment_center": "FC-West-04",
        "promised_delivery": "2026-10-12",
        "order_total": 648.0 if order_id.endswith("531") else 129.99,
    })


@tool
def get_customer(customer_id: str) -> str:
    """Retrieve customer service and delivery preferences by customer ID."""
    return json.dumps({"customer_id": customer_id, "tier": "standard", "preferred_contact": "email"})


@tool
def get_order_events(order_id: str) -> str:
    """Retrieve the order's fulfillment and customer-service event timeline."""
    events = [
        {"timestamp": "2026-10-09T08:10:00Z", "event": "Order placed"},
        {"timestamp": "2026-10-10T11:24:00Z", "event": "Picked and packed at FC-West-04"},
        {"timestamp": "2026-10-10T18:02:00Z", "event": "Carrier accepted shipment"},
    ]
    if order_id.endswith("482"):
        events.append({"timestamp": "2026-10-12T16:45:00Z", "event": "Delivery delayed: weather disruption"})
    return json.dumps(events)


@tool
def get_inventory(sku: str, fulfillment_center: str) -> str:
    """Check available and allocated stock for an item at a fulfillment center."""
    if sku == "SKU-OOS":
        return json.dumps({"sku": sku, "fulfillment_center": fulfillment_center, "on_hand": 0, "available": 0, "reserved": 0})
    return json.dumps({"sku": sku, "fulfillment_center": fulfillment_center, "on_hand": 8, "available": 6, "reserved": 2})


@tool
def get_inventory_movements(sku: str, fulfillment_center: str) -> str:
    """Retrieve recent receipts, picks, adjustments, and transfers for an item."""
    return json.dumps([{"timestamp": "2026-10-10T11:20:00Z", "type": "pick", "quantity": -1, "reference": "PICK-8812"}])


@tool
def get_stock_reservations(sku: str, fulfillment_center: str) -> str:
    """Retrieve active stock reservations for an item and fulfillment center."""
    return json.dumps([{"order_id": "ORD-10482", "sku": sku, "quantity": 1, "status": "allocated"}])


@tool
def get_tracking(order_id: str) -> str:
    """Retrieve the carrier tracking events and current shipment status."""
    if not order_id.endswith("482"):
        return json.dumps({"tracking_number": f"TRK-{order_id[-6:]}", "status": "in_transit", "last_scan": "Fulfillment center", "last_scan_at": "2026-10-10T18:02:00Z"})
    return json.dumps({"tracking_number": "TRK-700184", "status": "delayed", "last_scan": "Regional hub", "last_scan_at": "2026-10-12T16:45:00Z"})


@tool
def get_carrier_updates(tracking_number: str) -> str:
    """Retrieve carrier-provided service updates for a tracking number."""
    if tracking_number != "TRK-700184":
        return json.dumps([])
    return json.dumps([{"timestamp": "2026-10-12T16:45:00Z", "code": "WX", "description": "Weather disruption at regional hub"}])


@tool
def get_delivery_exception(order_id: str) -> str:
    """Retrieve the latest delivery exception details for an order."""
    if not order_id.endswith("482"):
        return json.dumps({"exception": None, "customer_contacted": False})
    return json.dumps({"exception": "Weather disruption", "first_seen": "2026-10-12T16:45:00Z", "customer_contacted": False})


@tool
def create_ticket(order_id: str, title: str, details: str) -> str:
    """Create an operations ticket after the recommendation has passed review."""
    return json.dumps({"ticket_id": f"OPS-{order_id[-5:]}", "status": "created", "title": title, "details": details})


@tool
def create_escalation(order_id: str, reason: str) -> str:
    """Create a supply-chain escalation for an order that needs intervention."""
    return json.dumps({"escalation_id": f"ESC-{order_id[-5:]}", "status": "created", "reason": reason})


@tool
def send_notification(recipient: str, message: str) -> str:
    """Queue an operational notification for delivery."""
    return json.dumps({"recipient": recipient, "status": "queued", "message": message})


@tool
def generate_case_report(order_id: str, summary: str) -> str:
    """Generate an audit-ready case report reference."""
    return json.dumps({"report_id": f"RPT-{order_id[-5:]}", "status": "generated", "summary": summary})


def invoke_tool(
    agent: str,
    selected_tool: Any,
    arguments: dict[str, Any],
    *,
    authorized_write: bool = False,
    context: dict | None = None,
) -> tuple[Any, dict[str, Any]]:
    context = context or {}
    span_id = str(uuid4())
    started = start_span(context, selected_tool.name, "tool", span_id)
    try:
        validate_tool_invocation(selected_tool.name, arguments, selected_tool.args_schema, authorized_write, context)
        raw_result = selected_tool.invoke(arguments)
        try:
            result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except json.JSONDecodeError:
            result = {"result": raw_result}
        end_span(context, selected_tool.name, "tool", span_id, started, "completed", {"agent_name": agent})
        emit_event(
            "tool.call",
            request_id=context.get("request_id"),
            trace_id=context.get("trace_id"),
            span_id=span_id,
            parent_span_id=context.get("root_span_id") or context.get("trace_id"),
            workflow_id=context.get("workflow_id"),
            user_id=context.get("user_id"),
            agent_name=agent,
            tool_name=selected_tool.name,
            status="success",
            details={"argument_keys": sorted(arguments), "result_keys": sorted(result) if isinstance(result, dict) else [], "write_authorized": authorized_write},
        )
        return result, {"agent": agent, "tool": selected_tool.name, "arguments": arguments, "result": result}
    except Exception as error:
        end_span(context, selected_tool.name, "tool", span_id, started, "error", {"agent_name": agent, "error_type": type(error).__name__})
        record_error(context, f"tool:{selected_tool.name}", error)
        emit_event(
            "tool.call",
            severity="ERROR",
            request_id=context.get("request_id"),
            trace_id=context.get("trace_id"),
            span_id=span_id,
            workflow_id=context.get("workflow_id"),
            user_id=context.get("user_id"),
            agent_name=agent,
            tool_name=selected_tool.name,
            status="error",
            details={"error_type": type(error).__name__},
        )
        raise