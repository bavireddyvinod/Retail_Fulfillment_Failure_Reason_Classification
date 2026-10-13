from fastapi import HTTPException, Request

from app.cache import consume_rate_limit
from app.config import settings
from app.guardrails import Principal, resolve_principal
from app.observability import emit_event


def current_principal(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else request.headers.get("x-api-key")
    principal = resolve_principal(token)
    if principal is None:
        emit_event("security.authentication", severity="WARNING", request_id=getattr(request.state, "request_id", None), trace_id=getattr(request.state, "trace_id", None), status="denied", details={"path": request.url.path, "reason_code": "invalid_or_missing_credentials"})
        raise HTTPException(status_code=401, detail="Authentication is required")
    count = consume_rate_limit(f"rate:{principal.user_id}:{request.url.path}", settings.api_rate_limit_per_minute)
    if count is None and settings.app_env.lower() in {"production", "prod"}:
        raise HTTPException(status_code=503, detail="Rate-limit service is unavailable")
    if count == 0:
        emit_event("security.rate_limit", severity="WARNING", request_id=getattr(request.state, "request_id", None), trace_id=getattr(request.state, "trace_id", None), user_id=principal.user_id, status="blocked", details={"path": request.url.path})
        raise HTTPException(status_code=429, detail="Request rate limit exceeded")
    return principal


def require_reviewer(principal: Principal) -> None:
    if not principal.has_role("reviewer"):
        raise HTTPException(status_code=403, detail="Reviewer permission is required")


def require_production_auth_configuration() -> None:
    if settings.app_env.lower() in {"production", "prod"} and not settings.api_keys:
        raise RuntimeError("API_KEYS_JSON must define production API principals")