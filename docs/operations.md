# Operations runbook

Everything an operator needs to run Pollie. It is true for the code in this repository.

## Processes

| Process | Command | Port | Needs |
|---------|---------|------|-------|
| api | `uvicorn pollie.api.main:app --host 0.0.0.0 --port 8000` (or `python -m pollie.api`) | 8000 | PostgreSQL, Redis |
| worker | `python -m pollie.worker` | 9100 (health, metrics) | PostgreSQL, Redis |
| web | Nginx serving `web/` and proxying `/api` to the API (development: `python web/devserver.py`) | 80 / 8080 | the API |
| PostgreSQL | 13 or newer | 5432 | |
| Redis | 6.2 or newer (`LMOVE`) | 6379 | |

All Python processes: Python 3.11 to 3.13, dependencies from `requirements.txt` (runtime only), unprivileged user, no files written. Shutdown is `SIGTERM`; the worker finishes the message in hand.

Start order: PostgreSQL and Redis, then api and worker (both fail at startup if a dependency is unreachable), then web.

## Health and readiness

| Endpoint | Where | Meaning | When it fails |
|----------|-------|---------|---------------|
| `/healthz` | api :8000, worker :9100 | the process is alive (worker: its loop ran in the last 30 s) | restart it |
| `/readyz` | api :8000, worker :9100 | PostgreSQL and Redis answer | stop sending traffic, fix the dependency, do not restart the process |

## Metrics (Prometheus)

| Metric | Where | Meaning |
|--------|-------|---------|
| `pollie_queue_depth{queue="queued\|processing\|dead"}` | api, worker | **The most important one.** `queued` growing means the worker is down or too slow. `dead` above 0 means votes failed for good. |
| `pollie_votes_accepted_total` | api | votes accepted into the queue |
| `pollie_worker_votes_total{result="recorded\|duplicate\|invalid\|retry\|dead"}` | worker | what the worker did with each vote |
| `pollie_worker_process_seconds` | worker | time per vote |
| `pollie_results_requests_total{source="cache\|database"}` | api | cache effectiveness |
| `pollie_api_http_requests_total`, `pollie_api_http_request_duration_seconds` | api | rate, errors, latency by route template |
| `pollie_polls_created_total`, `pollie_api_build_info`, `pollie_worker_build_info` | | counters and versions |

Starting alerts: `queued` above 100 for 5 minutes; `dead` above 0; 5xx ratio above 1%; p95 request duration above 500 ms; `/readyz` failing; worker `/healthz` failing.

## Logs

JSON, one object per line, on stdout. Each has `ts`, `level`, `service` (`api` or `worker`), `logger`, `msg`. API requests add `request_id`, `method`, `route`, `status`, `duration_ms`; worker votes add `result` and `poll_id`. Every API response carries `X-Request-ID` (yours is echoed), so a customer complaint maps to one log line.

## Failure modes

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Votes accepted (202) but results do not move | The worker is down or stuck. `pollie_queue_depth{queue="queued"}` grows. | Check the worker's `/healthz` and logs. Start it: the queue drains by itself. |
| `queued` stays high with a running worker | Worker is slower than the vote rate | Run more workers (they share the queue safely). |
| `dead` above 0 | Votes for unknown options, malformed messages, or the database failed `WORKER_MAX_ATTEMPTS` times | Inspect: `redis-cli LRANGE pollie:votes:dead 0 -1`. After fixing the cause, `LMOVE pollie:votes:dead pollie:votes RIGHT LEFT` re-queues one. |
| `/readyz` 503 on api | PostgreSQL or Redis unreachable | The `checks` field says which. Check network rules, credentials, the service. `/healthz` stays 200 on purpose. |
| api or worker exits at startup with `DATABASE_URL is required` | Missing environment | Set `DATABASE_URL` and `REDIS_URL`. |
| Worker logs `could not record vote` repeatedly | PostgreSQL is down or rejecting writes | Votes are retried with back-off and are not lost until the attempts run out. |
| After a worker crash a log says `requeued messages left by a previous worker` | Normal recovery | Nothing to do. |
| Web page loads but shows "API unreachable" | `/api` is not proxied to the API | Check the Nginx `location /api/` rule and that it strips the prefix. |
| 404 for `/api/polls` through Nginx | The proxy keeps the `/api` prefix | Use `proxy_pass http://api:8000/;` (with the trailing slash) so `/api/` is removed. |
| 422 on create or vote | Invalid input | The `detail` field says why. |

## Data

PostgreSQL holds everything that matters (`polls`, `options`, `votes`): back it up with `pg_dump`. Redis holds only in-flight votes and cache; losing it loses the votes that were queued but not yet recorded, so give it persistence (`appendonly yes`) if that matters.
