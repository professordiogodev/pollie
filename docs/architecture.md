# Architecture

## Components

```text
                    ┌───────────────┐
 Browser ─────────> │ web (Nginx)   │  static files, proxies /api
                    └──────┬────────┘
                           │ /api/*
                    ┌──────▼────────┐        results (cached)
                    │ api (FastAPI) │ <──────────────────────────┐
                    └──────┬────────┘                            │
              vote (queued)│  poll CRUD, results                 │
                    ┌──────▼────────┐        ┌──────────────┐    │
                    │ Redis         │        │ PostgreSQL   │ ───┘
                    │ queue + cache │        │ source of    │
                    └──────┬────────┘        │ truth        │
                           │ BLMOVE          └──────▲───────┘
                    ┌──────▼────────┐  INSERT vote   │
                    │ worker        │ ───────────────┘
                    └───────────────┘
```

## Request flows

**Vote** `POST /polls/{id}/votes`

1. The API checks the poll and the option, then pushes `{poll_id, option_id, voter_id, attempts}` to the Redis list `pollie:votes` and answers `202`.
2. A worker moves the message to `pollie:votes:processing` (`BLMOVE`) and inserts the vote with `ON CONFLICT DO NOTHING`.
3. `recorded` and `duplicate` are acknowledged. An unknown option or a malformed message goes to `pollie:votes:dead`. A database error is retried up to `WORKER_MAX_ATTEMPTS` times with back-off, then goes to the dead letter list.

**Results** `GET /polls/{id}/results`: Redis cache first (`RESULTS_CACHE_TTL` seconds), otherwise a grouped query on PostgreSQL. A broken cache degrades to database reads.

## Design decisions

| Decision | Why |
|----------|-----|
| Votes go through a queue | The API answers fast under load and keeps accepting votes when PostgreSQL is slow or the worker is down. The cost is that results lag by a moment. |
| Reliable queue (`BLMOVE` to a processing list) | A crashed worker loses nothing: leftovers are requeued at the next start. At-least-once delivery, so recording a vote must be idempotent (unique `(poll_id, voter_id)`). |
| API and worker are one codebase | One image, two commands. Simple to build, version and release together. |
| Liveness does not check dependencies, readiness does | A database outage takes instances out of rotation without restarting healthy processes. |
| Frontend calls a relative `/api` | The same files work behind any host. Nginx (or an ingress) decides where `/api` goes. |
| Plain SQL, no ORM | Short enough to read in one sitting. |
| Schema created at startup | Keeps the project small. A real system uses migrations (a good exercise). |
| Metric labels use route templates | `/polls/{poll_id}` instead of each id, so cardinality stays bounded. |
