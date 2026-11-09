# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/), versions follow [Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-11-09

### Fixed
- The worker crashed when Redis went away. It now stays alive, waits with back-off, and resumes when Redis is back (`pollie_worker_queue_errors_total`).
- Voting while Redis was down answered `500`. It now answers `503` with a message the client can act on.

## [0.1.0] - 2026-11-09

### Added
- API (FastAPI): create polls, list and read polls, queue votes (202), cached results.
- Worker: records queued votes in PostgreSQL, idempotent, retries with back-off, dead letter queue, recovers messages left by a crashed worker.
- Static web frontend and a development server that proxies `/api`.
- `/healthz`, `/readyz` and `/metrics` on the API and the worker; JSON logs with request ids.
- Unit tests (no services), integration tests against real PostgreSQL and Redis, CI, `.env.example`, architecture and operations docs, and the usual project files.
