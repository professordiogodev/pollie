"""Pollie worker: takes votes from the Redis queue and records them in PostgreSQL.

Run:  python -m pollie.worker
Exposes /healthz, /readyz and /metrics on WORKER_METRICS_PORT (default 9100).
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from pollie.common.config import Settings
from pollie.common.db import Database, PostgresDatabase
from pollie.common.logging_config import configure_logging
from pollie.common.queue import QueueUnavailable, RedisVoteQueue, VoteQueue

log = logging.getLogger("pollie.worker")


class Worker:
    def __init__(self, settings: Settings, db: Database, queue: VoteQueue) -> None:
        self.settings, self.db, self.queue = settings, db, queue
        self.registry = CollectorRegistry()
        self.processed = Counter("pollie_worker_votes_total", "Votes processed", ["result"], registry=self.registry)
        self.seconds = Histogram("pollie_worker_process_seconds", "Time to process one vote", registry=self.registry)
        self.queue_errors = Counter(
            "pollie_worker_queue_errors_total", "Times the queue could not be reached", registry=self.registry
        )
        self.depth = Gauge("pollie_worker_queue_depth", "Messages per queue", ["queue"], registry=self.registry)
        Gauge("pollie_worker_build_info", "Build information", ["version"], registry=self.registry).labels(
            version=settings.app_version
        ).set(1)
        self.last_beat = time.time()
        self.stop = threading.Event()
        self._requeued = False

    def process_one(self, timeout: int | None = None) -> str | None:
        """Handle at most one message. Returns the result label, or None if the queue was empty."""
        self.last_beat = time.time()
        item = self.queue.pop(self.settings.worker_poll_seconds if timeout is None else timeout)
        if item is None:
            return None
        raw, message = item
        started = time.perf_counter()
        try:
            result = self.db.record_vote(int(message["poll_id"]), int(message["option_id"]), str(message["voter_id"]))
        except (KeyError, ValueError, TypeError):
            result = "invalid"
        except Exception:
            attempts = int(message.get("attempts", 0)) + 1
            log.exception("could not record vote", extra={"attempts": attempts})
            if attempts >= self.settings.worker_max_attempts:
                self.queue.dead_letter(raw)
                self.processed.labels("dead").inc()
                return "dead"
            self.queue.retry(raw, {**message, "attempts": attempts})
            self.processed.labels("retry").inc()
            time.sleep(min(2**attempts, 10) if not self.stop.is_set() else 0)
            return "retry"
        if result == "invalid":
            self.queue.dead_letter(raw)
        else:
            self.queue.ack(raw)
        self.processed.labels(result).inc()
        self.seconds.observe(time.perf_counter() - started)
        log.info("vote", extra={"result": result, "poll_id": message.get("poll_id")})
        return result

    def run(self) -> None:
        log.info("worker started", extra={"version": self.settings.app_version})
        waiting_since: float | None = None
        failures = 0
        while not self.stop.is_set():
            try:
                if not self._requeued:
                    moved = self.queue.requeue_stale()
                    self._requeued = True
                    if moved:
                        log.warning("requeued messages left by a previous worker", extra={"count": moved})
                self.process_one()
                if waiting_since is not None:
                    log.info("queue is back", extra={"seconds_down": round(time.time() - waiting_since)})
                    waiting_since, failures = None, 0
            except QueueUnavailable as exc:
                # Redis is down or restarting: stay alive, keep trying, and let /readyz say what is wrong.
                self.last_beat = time.time()
                self.queue_errors.inc()
                if waiting_since is None:
                    waiting_since = time.time()
                    log.error("queue unavailable, waiting", extra={"error": str(exc)})
                failures += 1
                self.stop.wait(min(2**failures, 10))
        log.info("worker stopped")

    def healthy(self) -> bool:
        return (time.time() - self.last_beat) < max(30, self.settings.worker_poll_seconds * 5)

    def metrics_text(self) -> bytes:
        try:
            for name, value in self.queue.depth().items():
                self.depth.labels(name).set(value)
        except Exception:
            log.warning("could not read queue depth")
        return generate_latest(self.registry)


def make_http_server(worker: Worker, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                ok = worker.healthy()
                self._send(200 if ok else 503, json.dumps({"status": "ok" if ok else "stalled", "service": "worker"}).encode())
            elif self.path == "/readyz":
                checks: dict[str, Any] = {"database": worker.db.ping(), "redis": worker.queue.ping()}
                ok = all(checks.values())
                self._send(200 if ok else 503, json.dumps({"status": "ready" if ok else "not_ready", "checks": checks}).encode())
            elif self.path == "/metrics":
                self._send(200, worker.metrics_text(), CONTENT_TYPE_LATEST)
            else:
                self._send(404, b'{"detail":"not found"}')

        def log_message(self, *args: Any) -> None:
            return None

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104


def main() -> None:
    settings = Settings.from_env()
    configure_logging("worker", settings.log_level, settings.log_format)
    db = PostgresDatabase(settings.database_url)
    db.init_schema()
    worker = Worker(settings, db, RedisVoteQueue(settings.redis_url))
    server = make_http_server(worker, settings.worker_metrics_port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop.set())
    try:
        worker.run()
    finally:
        server.shutdown()
        db.close()
