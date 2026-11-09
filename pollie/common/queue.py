"""A small reliable queue on Redis lists.

API -> `pollie:votes` -> worker. A worker moves a message to `pollie:votes:processing` while it works on it
(at-least-once delivery), removes it when done, and sends poison messages to `pollie:votes:dead`.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

MAIN, PROCESSING, DEAD = "pollie:votes", "pollie:votes:processing", "pollie:votes:dead"


class VoteQueue(Protocol):
    def push(self, message: dict[str, Any]) -> None: ...
    def pop(self, timeout: int) -> tuple[str, dict[str, Any]] | None: ...
    def ack(self, raw: str) -> None: ...
    def retry(self, raw: str, message: dict[str, Any]) -> None: ...
    def dead_letter(self, raw: str) -> None: ...
    def requeue_stale(self) -> int: ...
    def depth(self) -> dict[str, int]: ...
    def ping(self) -> bool: ...


class RedisVoteQueue:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=10)

    def push(self, message: dict[str, Any]) -> None:
        self._r.lpush(MAIN, json.dumps(message))

    def pop(self, timeout: int) -> tuple[str, dict[str, Any]] | None:
        raw = self._r.blmove(MAIN, PROCESSING, timeout, "RIGHT", "LEFT")
        if raw is None:
            return None
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            self.dead_letter(raw)
            return None

    def ack(self, raw: str) -> None:
        self._r.lrem(PROCESSING, 1, raw)

    def retry(self, raw: str, message: dict[str, Any]) -> None:
        pipe = self._r.pipeline()
        pipe.lrem(PROCESSING, 1, raw)
        pipe.lpush(MAIN, json.dumps(message))
        pipe.execute()

    def dead_letter(self, raw: str) -> None:
        pipe = self._r.pipeline()
        pipe.lrem(PROCESSING, 1, raw)
        pipe.lpush(DEAD, raw)
        pipe.execute()

    def requeue_stale(self) -> int:
        """At startup: messages left in `processing` by a crashed worker go back to the queue."""
        moved = 0
        while self._r.lmove(PROCESSING, MAIN, "RIGHT", "LEFT") is not None:
            moved += 1
        return moved

    def depth(self) -> dict[str, int]:
        return {"queued": self._r.llen(MAIN), "processing": self._r.llen(PROCESSING), "dead": self._r.llen(DEAD)}

    def ping(self) -> bool:
        try:
            return bool(self._r.ping())
        except Exception:
            return False


class ResultsCache(Protocol):
    def get(self, poll_id: int) -> list[dict[str, Any]] | None: ...
    def set(self, poll_id: int, results: list[dict[str, Any]], ttl: int) -> None: ...


class RedisResultsCache:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)

    def get(self, poll_id: int) -> list[dict[str, Any]] | None:
        try:
            raw = self._r.get(f"pollie:results:{poll_id}")
            return json.loads(raw) if raw else None
        except Exception:  # a broken cache never breaks results
            return None

    def set(self, poll_id: int, results: list[dict[str, Any]], ttl: int) -> None:
        try:
            self._r.set(f"pollie:results:{poll_id}", json.dumps(results), ex=ttl)
        except Exception:
            return None
