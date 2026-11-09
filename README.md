# Pollie

[![CI](https://github.com/professordiogodev/pollie/actions/workflows/ci.yml/badge.svg)](https://github.com/professordiogodev/pollie/actions/workflows/ci.yml)

A small polls application: create a poll, share it, watch the votes come in. It is the application you containerise, configure, test and ship in weeks 4 to 6 of the Ironhack DevOps & Cloud Engineering bootcamp.

It has **five moving parts on purpose**, so that you need Docker Compose, a pipeline that builds several images, and a deployment that is more than one process:

```text
Browser ──> web (static files, Nginx in production) ──/api──> api (FastAPI, port 8000)
                                                               │  queue a vote          ▲ results
                                                               ▼                        │
                                                            Redis ──> worker ──> PostgreSQL
```

| Part | What it does | Port |
|------|--------------|------|
| `web/` | Static single-page app (HTML, CSS, JavaScript, no build step). Calls the API on the relative path `/api`. | 8080 in development |
| `pollie/api` | FastAPI. Creates polls, accepts votes (202, queued), serves cached results. | 8000 |
| `pollie/worker` | Takes votes from the Redis queue and records them in PostgreSQL. Exposes health and metrics. | 9100 |
| PostgreSQL | Polls, options and votes. | 5432 |
| Redis | The vote queue and a short-lived results cache. | 6379 |

What it does **not** have: a Dockerfile, a compose file, Ansible, Terraform, a deployment pipeline or Kubernetes manifests. Those are your job.

## Quick start (development)

You need Python 3.11 to 3.13, PostgreSQL and Redis (install them locally or run them any way you like).

```bash
git clone https://github.com/professordiogodev/pollie.git
cd pollie
python3 -m venv venv && source venv/bin/activate
make install-dev
make env                      # creates .env from .env.example; edit DATABASE_URL and REDIS_URL
set -a; source .env; set +a   # export the settings into your shell

make run-api                  # terminal 1: http://localhost:8000/docs
make run-worker               # terminal 2: worker, metrics on :9100
make run-web                  # terminal 3: http://localhost:8080
```

Try it without the browser:

```bash
curl -s -X POST localhost:8000/polls -H 'Content-Type: application/json' \
  -d '{"question": "Tabs or spaces?", "options": ["Tabs", "Spaces"]}'
curl -s -X POST localhost:8000/polls/1/votes -H 'Content-Type: application/json' \
  -d '{"option_id": 1, "voter_id": "me"}'                        # 202 queued
curl -s localhost:8000/polls/1/results                           # the worker recorded it
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/polls` | Create a poll (`question`, 2 to `MAX_OPTIONS` `options`). 422 for invalid input. |
| `GET` | `/polls` | Latest polls. |
| `GET` | `/polls/{id}` | One poll with its options. |
| `POST` | `/polls/{id}/votes` | Cast a vote (`option_id`, `voter_id`). Answers **202**: it is queued and the worker records it. One vote per `voter_id` per poll. |
| `GET` | `/polls/{id}/results` | Votes and percentages. Cached for `RESULTS_CACHE_TTL` seconds. |
| `GET` | `/healthz` | **Liveness.** The process is up. Never calls PostgreSQL or Redis. |
| `GET` | `/readyz` | **Readiness.** 503 when PostgreSQL or Redis do not answer. |
| `GET` | `/metrics` | Prometheus metrics, including `pollie_queue_depth`. |

The worker serves `/healthz`, `/readyz` and `/metrics` on `WORKER_METRICS_PORT` (9100).

## Configuration

Environment variables only. `.env.example` lists all of them with comments. `.env` is ignored by git: never commit it.

| Variable | Default | Used by | Description |
|----------|---------|---------|-------------|
| `DATABASE_URL` | **required** | api, worker | `postgresql://USER:PASSWORD@HOST:5432/DATABASE`. The tables are created on startup. |
| `REDIS_URL` | **required** | api, worker | `redis://HOST:6379` |
| `APP_VERSION` | package version | all | Reported by `/healthz`, `/metrics`, logs. Set it to the image tag in your pipeline. |
| `LOG_LEVEL`, `LOG_FORMAT` | `info`, `json` | all | `debug\|info\|warn\|error`, `json\|text` |
| `API_HOST`, `API_PORT` | `0.0.0.0`, `8000` | api | Only for `python -m pollie.api`. |
| `CORS_ORIGINS` | empty | api | Comma-separated origins. Leave empty when the web container proxies `/api`. |
| `MAX_OPTIONS`, `MAX_QUESTION_LENGTH` | `10`, `200` | api | Input limits. |
| `RESULTS_CACHE_TTL` | `5` | api | Seconds results stay in Redis. |
| `WORKER_METRICS_PORT` | `9100` | worker | Health and metrics port. |
| `WORKER_POLL_SECONDS` | `2` | worker | How long the worker waits for a message before looping. |
| `WORKER_MAX_ATTEMPTS` | `5` | worker | After this many failed attempts a vote goes to the dead letter queue. |
| `API_URL`, `WEB_PORT` | `http://localhost:8000`, `8080` | `web/devserver.py` | Development server only. |

## Things worth knowing as a DevOps engineer

- **The API and the worker are the same codebase and can share one image.** They start with different commands: `uvicorn pollie.api.main:app --host 0.0.0.0 --port 8000` and `python -m pollie.worker`.
- **If the worker is down the API keeps accepting votes.** They wait in Redis (`pollie_queue_depth{queue="queued"}` grows) and are recorded when a worker returns. This is the thing to monitor.
- **A worker that crashes mid-vote loses nothing.** The message sits in `pollie:votes:processing` and is requeued when a worker starts.
- **Both the API and the worker need PostgreSQL and Redis at startup.** Start them first, or let your orchestrator retry.
- **The web app talks to `/api` on its own origin.** In production something (Nginx) must serve the files and proxy `/api` to the API, stripping the prefix. `web/devserver.py` does exactly that for development.
- **Votes are deduplicated by `voter_id`** (stored in the browser), so a voter cannot vote twice. It is a teaching device, not authentication.

## Development

```bash
make help
make test                # unit tests + coverage (no services needed)
make test-integration    # real PostgreSQL and Redis (DATABASE_URL and REDIS_URL set)
make lint                # ruff
make check               # lint + tests: what CI runs
```

```text
pollie/
  api/main.py            routes, validation, metrics
  worker/main.py         the vote worker and its health server
  common/                configuration, PostgreSQL access, Redis queue and cache, logging
web/                     the static frontend and a development server
tests/                   unit tests (fakes) and tests/integration (real services)
docs/                    architecture and operations runbook
```

## Documentation

- [Architecture](docs/architecture.md)
- [Operations runbook](docs/operations.md): ports, health, metrics, logs, failure modes
- [Contributing](CONTRIBUTING.md), [Changelog](CHANGELOG.md), [Security policy](SECURITY.md)

## How Pollie is used in the bootcamp

| Week | What you do with it |
|------|---------------------|
| 4 | Write the Dockerfiles, a compose file for the whole stack, health checks, networks and volumes |
| 5 | Configure servers with Ansible, build CI/CD workflows, run the worker as a serverless function experiment |
| 6 (Project 2) | Ship it through a secure pipeline: build several images, scan, test, approve, deploy, roll back |

## License

MIT, see [LICENSE](LICENSE).
