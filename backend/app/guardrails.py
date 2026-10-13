import re
from dataclasses import dataclass
from typing import Any

from app.config import settings

READ_TOOLS = frozenset({
    "get_order", "get_customer", "get_order_events", "get_inventory",
    "get_inventory_movements", "get_stock_reservations", "get_tracking",
    "get_carrier_updates", "get_delivery_exception",
})
WRITE_TOOLS = frozenset({"create_ticket", "create_escalation", "send_notification", "generate_case_report"})
ALLOWED_TOOLS = READ_TOOLS | WRITE_TOOLS

_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|system)\s+instructions", re.IGNORECASE),
    re.compile(r"reveal\s+(the\s+)?(system prompt|hidden prompt|api key|secret|credentials)", re.IGNORECASE),
    re.compile(r"(bypass|disable|override)\s+(the\s+)?(approval|guardrail|security|permission)", re.IGNORECASE),
    re.compile(r"execute\s+(arbitrary|unapproved|all)\s+tools?", re.IGNORECASE),
)
_SENSITIVE_DISCLOSURE = re.compile(r"(show|reveal|export|list|give\s+me).{0,50}(social security|ssn|credit card|payment card|password|access token|api key|all customer records)", re.IGNORECASE)


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: frozenset[str]
    authenticated: bool

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


def resolve_principal(token: str | None, app_env: str | None = None) -> Principal | None:
    configured = settings.api_keys
    if token and token in configured:
        entry = configured[token]
        if isinstance(entry, str):
            entry = {"user_id": entry, "roles": ["analyst"]}
        roles = entry.get("roles", []) if isinstance(entry, dict) else []
        user_id = entry.get("user_id", "service-user") if isinstance(entry, dict) else "service-user"
        return Principal(user_id=user_id, roles=frozenset(roles), authenticated=True)
    if (app_env or settings.app_env).lower() not in {"production", "prod"}:
        return Principal(user_id="local-operator", roles=frozenset({"analyst", "reviewer"}), authenticated=False)
    return None


def inspect_input(query: str, order_id: str | None) -> dict[str, Any]:
    if not query.strip():
        return {"decision": "BLOCK", "reason_code": "empty_query", "severity": "low"}
    if len(query) > 4000:
        return {"decision": "BLOCK", "reason_code": "payload_too_large", "severity": "medium"}
    if order_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,79}", order_id):
        return {"decision": "BLOCK", "reason_code": "invalid_order_id", "severity": "medium"}
    if any(pattern.search(query) for pattern in _INJECTION_PATTERNS):
        return {"decision": "BLOCK", "reason_code": "instruction_override_attempt", "severity": "high"}
    if _SENSITIVE_DISCLOSURE.search(query):
        return {"decision": "BLOCK", "reason_code": "sensitive_data_disclosure_request", "severity": "high"}
    return {"decision": "PASS", "reason_code": "fulfillment_scope_validated", "severity": "info"}


def validate_tool_invocation(tool_name: str, arguments: dict[str, Any], tool_schema: Any, authorized_write: bool, resource_context: dict | None = None) -> None:
    if tool_name not in ALLOWED_TOOLS:
        raise PermissionError(f"Tool '{tool_name}' is not in the approved tool allowlist")
    if tool_name in WRITE_TOOLS and not authorized_write:
        raise PermissionError(f"Write tool '{tool_name}' requires explicit action approval")
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    tool_schema.model_validate(arguments)
    resource_context = resource_context or {}
    order = resource_context.get("fulfillment_data", {}).get("order", {})
    if arguments.get("order_id") and resource_context.get("order_id") and arguments["order_id"] != resource_context["order_id"]:
        raise PermissionError("Tool target order does not match the authorized investigation")
    if arguments.get("sku") and order.get("items") and arguments["sku"] not in {item.get("sku") for item in order["items"]}:
        raise PermissionError("Tool target SKU does not belong to the investigated order")
    if arguments.get("fulfillment_center") and order.get("fulfillment_center") and arguments["fulfillment_center"] != order["fulfillment_center"]:
        raise PermissionError("Tool target fulfillment center does not match the investigated order")
    if arguments.get("customer_id") and resource_context.get("fulfillment_data", {}).get("customer", {}).get("customer_id") and arguments["customer_id"] != resource_context["fulfillment_data"]["customer"]["customer_id"]:
        raise PermissionError("Tool target customer does not match the authorized investigation")


def validate_approved_actions(proposed: list[dict[str, Any]], approved_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {item.get("action_id"): item for item in proposed}
    if not approved_ids or len(set(approved_ids)) != len(approved_ids):
        raise ValueError("Approval must name one or more unique proposed action IDs")
    if any(action_id not in by_id for action_id in approved_ids):
        raise ValueError("Approval includes an action that was not proposed for this workflow")
    return [by_id[action_id] for action_id in approved_ids]