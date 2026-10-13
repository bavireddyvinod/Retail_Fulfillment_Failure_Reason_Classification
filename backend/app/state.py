from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    workflow_id: str
    session_id: str
    user_id: str
    user_query: str
    order_id: str
    conversation_history: list[dict[str, Any]]
    triage: dict[str, Any]
    fulfillment_data: dict[str, Any]
    inventory_data: dict[str, Any]
    shipment_data: dict[str, Any]
    rag_documents: list[dict[str, Any]]
    retrieved_context: str
    classification_result: dict[str, Any]
    investigation_result: dict[str, Any]
    proposed_actions: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    confidence: float
    validation_result: dict[str, Any]
    human_approval: dict[str, Any]
    citations: list[dict[str, Any]]
    errors: list[str]
    agent_trace: list[dict[str, Any]]
    retry_count: int
    final_response: dict[str, Any]
    status: str
    blocked: bool