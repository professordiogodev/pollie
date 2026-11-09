"""Pollie API.

POST /polls                 create a poll
GET  /polls                 latest polls
GET  /polls/{id}            one poll
POST /polls/{id}/votes      cast a vote (202: queued, the worker records it)
GET  /polls/{id}/results    results (cached for a few seconds in Redis)
GET  /healthz               liveness
GET  /readyz                readiness (PostgreSQL and Redis)
GET  /metrics               Prometheus metrics
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel

from pollie.common.config import Settings
from pollie.common.db import Database, PostgresDatabase
from pollie.common.logging_config import configure_logging
from pollie.common.queue import RedisResultsCache, RedisVoteQueue, ResultsCache, VoteQueue

log = logging.getLogger("pollie.api")


class PollIn(BaseModel):
    question: str
    options: list[str]


class VoteIn(BaseModel):
    option_id: int
    voter_id: str


def clean_poll(data: PollIn, settings: Settings) -> tuple[str, list[str]]:
    question = data.question.strip()
    options = [o.strip() for o in data.options]
    problem = None
    if not question:
        problem = "question must not be empty"
    elif len(question) > settings.max_question_length:
        problem = f"question is longer than {settings.max_question_length} characters"
    elif len(options) < 2:
        problem = "a poll needs at least 2 options"
    elif len(options) > settings.max_options:
        problem = f"a poll can have at most {settings.max_options} options"
    elif any(not o or len(o) > 100 for o in options):
        problem = "every option must be 1 to 100 characters"
    elif len({o.lower() for o in options}) != len(options):
        problem = "options must be different from each other"
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    return question, options


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    queue: VoteQueue | None = None,
    cache: ResultsCache | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    registry = CollectorRegistry()
    http_total = Counter("pollie_api_http_requests_total", "HTTP requests", ["method", "path", "status"], registry=registry)
    http_seconds = Histogram(
        "pollie_api_http_request_duration_seconds", "HTTP request duration", ["method", "path"], registry=registry
    )
    polls_created = Counter("pollie_polls_created_total", "Polls created", registry=registry)
    votes_accepted = Counter("pollie_votes_accepted_total", "Votes accepted into the queue", registry=registry)
    results_total = Counter("pollie_results_requests_total", "Results requests", ["source"], registry=registry)
    queue_depth = Gauge("pollie_queue_depth", "Messages per queue", ["queue"], registry=registry)
    Gauge("pollie_api_build_info", "Build information", ["version"], registry=registry).labels(version=settings.app_version).set(
        1
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging("api", settings.log_level, settings.log_format)
        app.state.db = db or PostgresDatabase(settings.database_url)
        app.state.queue = queue or RedisVoteQueue(settings.redis_url)
        app.state.cache = cache or RedisResultsCache(settings.redis_url)
        app.state.db.init_schema()
        log.info("starting", extra={"version": settings.app_version})
        yield
        app.state.db.close()
        log.info("stopped")

    app = FastAPI(
        title="Pollie API", description="Polls for the Ironhack DevOps bootcamp", version=settings.app_version, lifespan=lifespan
    )
    app.state.started = time.time()
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def observe(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started, status = time.perf_counter(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            path = route.path if route else "unmatched"
            http_total.labels(request.method, path, str(status)).inc()
            http_seconds.labels(request.method, path).observe(elapsed)
            if path not in ("/metrics", "/healthz", "/readyz"):
                log.info(
                    "request",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "route": path,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        """Liveness: the process is up. Never calls PostgreSQL or Redis."""
        return {
            "status": "ok",
            "service": "api",
            "version": settings.app_version,
            "uptime_seconds": round(time.time() - app.state.started, 2),
        }

    @app.get("/readyz", tags=["ops"])
    def readyz(response: Response) -> dict:
        """Readiness: PostgreSQL and Redis answer. 503 otherwise."""
        checks = {"database": app.state.db.ping(), "redis": app.state.queue.ping()}
        ok = all(checks.values())
        if not ok:
            response.status_code = 503
        return {"status": "ready" if ok else "not_ready", "checks": checks}

    @app.post("/polls", status_code=201, tags=["api"])
    def create_poll(data: PollIn) -> dict:
        question, options = clean_poll(data, settings)
        poll = app.state.db.create_poll(question, options)
        polls_created.inc()
        return poll

    @app.get("/polls", tags=["api"])
    def list_polls() -> list[dict]:
        return app.state.db.list_polls()

    @app.get("/polls/{poll_id}", tags=["api"])
    def get_poll(poll_id: int) -> dict:
        poll = app.state.db.get_poll(poll_id)
        if not poll:
            raise HTTPException(status_code=404, detail="poll not found")
        return poll

    @app.post("/polls/{poll_id}/votes", status_code=202, tags=["api"])
    def vote(poll_id: int, data: VoteIn) -> dict:
        """Accept a vote. It is queued and recorded by the worker, so this answers fast even under load."""
        poll = app.state.db.get_poll(poll_id)
        if not poll:
            raise HTTPException(status_code=404, detail="poll not found")
        if data.option_id not in {o["id"] for o in poll["options"]}:
            raise HTTPException(status_code=422, detail="option does not belong to this poll")
        voter = data.voter_id.strip()
        if not voter or len(voter) > 64:
            raise HTTPException(status_code=422, detail="voter_id must be 1 to 64 characters")
        app.state.queue.push({"poll_id": poll_id, "option_id": data.option_id, "voter_id": voter, "attempts": 0})
        votes_accepted.inc()
        return {"status": "queued"}

    @app.get("/polls/{poll_id}/results", tags=["api"])
    def results(poll_id: int) -> dict:
        poll = app.state.db.get_poll(poll_id)
        if not poll:
            raise HTTPException(status_code=404, detail="poll not found")
        rows = app.state.cache.get(poll_id)
        source = "cache"
        if rows is None:
            rows = app.state.db.results(poll_id)
            app.state.cache.set(poll_id, rows, settings.results_cache_ttl)
            source = "database"
        results_total.labels(source).inc()
        total = sum(r["votes"] for r in rows)
        return {
            "poll": poll,
            "total_votes": total,
            "source": source,
            "results": [{**r, "percent": round(100 * r["votes"] / total, 1) if total else 0.0} for r in rows],
        }

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def metrics() -> Response:
        try:
            for name, value in app.state.queue.depth().items():
                queue_depth.labels(name).set(value)
        except Exception:
            log.warning("could not read queue depth")
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    return app


def get_app() -> FastAPI:
    """Factory for `uvicorn pollie.api.main:get_app --factory` and the module-level `app`."""
    return create_app()


try:  # a module-level `app` for `uvicorn pollie.api.main:app` when the environment is configured
    app = create_app()
except ValueError:  # missing DATABASE_URL or REDIS_URL while importing for tests or tooling
    app = None  # type: ignore[assignment]
