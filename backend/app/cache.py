import json
from typing import Any

import redis

from app.config import settings

_client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=0.2,
    socket_timeout=0.2,
)
DASHBOARD_KEY = "retail-fulfillment:dashboard:v1"


def get_dashboard() -> dict[str, Any] | None:
    try:
        value = _client.get(DASHBOARD_KEY)
        return json.loads(value) if value else None
    except (redis.RedisError, json.JSONDecodeError):
        return None


def set_dashboard(value: dict[str, Any]) -> None:
    try:
        _client.setex(DASHBOARD_KEY, 15, json.dumps(value))
    except redis.RedisError:
        pass


def invalidate_dashboard() -> None:
    try:
        _client.delete(DASHBOARD_KEY)
    except redis.RedisError:
        pass


def redis_health() -> str:
    try:
        return "ok" if _client.ping() else "unavailable"
    except redis.RedisError:
        return "unavailable"


def consume_rate_limit(key: str, limit: int, window_seconds: int = 60) -> int | None:
    try:
        pipe = _client.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count) if count <= limit else 0
    except redis.RedisError:
        return None