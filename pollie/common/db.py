"""PostgreSQL access. Plain SQL, no ORM, so students can read every query."""

from __future__ import annotations

from typing import Any, Protocol

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id         serial PRIMARY KEY,
    question   text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS options (
    id       serial PRIMARY KEY,
    poll_id  integer NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    label    text NOT NULL,
    position integer NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
    id         bigserial PRIMARY KEY,
    poll_id    integer NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
    option_id  integer NOT NULL REFERENCES options(id) ON DELETE CASCADE,
    voter_id   text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (poll_id, voter_id)
);
CREATE INDEX IF NOT EXISTS votes_option_idx ON votes (option_id);
"""


class Database(Protocol):
    def init_schema(self) -> None: ...
    def create_poll(self, question: str, options: list[str]) -> dict[str, Any]: ...
    def get_poll(self, poll_id: int) -> dict[str, Any] | None: ...
    def list_polls(self, limit: int = 50) -> list[dict[str, Any]]: ...
    def results(self, poll_id: int) -> list[dict[str, Any]]: ...
    def record_vote(self, poll_id: int, option_id: int, voter_id: str) -> str:
        """Return 'recorded', 'duplicate' or 'invalid'."""

    def ping(self) -> bool: ...
    def close(self) -> None: ...


class PostgresDatabase:
    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True, timeout=5)

    def init_schema(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)

    def create_poll(self, question: str, options: list[str]) -> dict[str, Any]:
        with self._pool.connection() as conn:
            poll_id = conn.execute("INSERT INTO polls (question) VALUES (%s) RETURNING id", (question,)).fetchone()[0]
            for position, label in enumerate(options):
                conn.execute("INSERT INTO options (poll_id, label, position) VALUES (%s, %s, %s)", (poll_id, label, position))
        return self.get_poll(poll_id)  # type: ignore[return-value]

    def get_poll(self, poll_id: int) -> dict[str, Any] | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT id, question, created_at FROM polls WHERE id = %s", (poll_id,)).fetchone()
            if not row:
                return None
            opts = conn.execute("SELECT id, label FROM options WHERE poll_id = %s ORDER BY position", (poll_id,)).fetchall()
        return {
            "id": row[0],
            "question": row[1],
            "created_at": row[2].isoformat(),
            "options": [{"id": o[0], "label": o[1]} for o in opts],
        }

    def list_polls(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT id FROM polls ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [p for p in (self.get_poll(r[0]) for r in rows) if p]

    def results(self, poll_id: int) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT o.id, o.label, count(v.id)
                   FROM options o LEFT JOIN votes v ON v.option_id = o.id
                   WHERE o.poll_id = %s GROUP BY o.id, o.label, o.position ORDER BY o.position""",
                (poll_id,),
            ).fetchall()
        return [{"option_id": r[0], "label": r[1], "votes": r[2]} for r in rows]

    def record_vote(self, poll_id: int, option_id: int, voter_id: str) -> str:
        with self._pool.connection() as conn:
            valid = conn.execute("SELECT 1 FROM options WHERE id = %s AND poll_id = %s", (option_id, poll_id)).fetchone()
            if not valid:
                return "invalid"
            cur = conn.execute(
                "INSERT INTO votes (poll_id, option_id, voter_id) VALUES (%s, %s, %s) ON CONFLICT (poll_id, voter_id) DO NOTHING",
                (poll_id, option_id, voter_id),
            )
            return "recorded" if cur.rowcount == 1 else "duplicate"

    def ping(self) -> bool:
        try:
            with self._pool.connection(timeout=2) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._pool.close()
