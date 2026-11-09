# Changelog

All notable changes. Format: [Keep a Changelog](https://keepachangelog.com/), versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-09

### Added
- API (FastAPI): create polls, list and read polls, queue votes (202), cached results.
- Worker: records queued votes in PostgreSQL, idempotent, retries with back-off, dead letter queue, recovers messages left by a crashed worker.
- Static web frontend and a development server that proxies `/api`.
- `/healthz`, `/readyz` and `/metrics` on the API and the worker; JSON logs with request ids.
- Unit tests (no services), integration tests against real PostgreSQL and Redis, CI, `.env.example`, architecture and operations docs, and the usual project files.
