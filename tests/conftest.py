import pytest
from fastapi.testclient import TestClient

from pollie.api.main import create_app
from pollie.common.config import Settings


class FakeDB:
    def __init__(self):
        self.polls, self.votes, self.healthy, self.fail_votes = {}, {}, True, False
        self._next_poll, self._next_opt = 1, 1

    def init_schema(self):
        return None

    def create_poll(self, question, options):
        pid = self._next_poll
        self._next_poll += 1
        opts = []
        for label in options:
            opts.append({"id": self._next_opt, "label": label})
            self._next_opt += 1
        self.polls[pid] = {"id": pid, "question": question, "created_at": "2026-01-01T00:00:00+00:00", "options": opts}
        return self.polls[pid]

    def get_poll(self, poll_id):
        return self.polls.get(poll_id)

    def list_polls(self, limit=50):
        return list(reversed(list(self.polls.values())))[:limit]

    def results(self, poll_id):
        poll = self.polls[poll_id]
        return [
            {
                "option_id": o["id"],
                "label": o["label"],
                "votes": sum(1 for (p, _), oid in self.votes.items() if p == poll_id and oid == o["id"]),
            }
            for o in poll["options"]
        ]

    def record_vote(self, poll_id, option_id, voter_id):
        if self.fail_votes:
            raise ConnectionError("db down")
        poll = self.polls.get(poll_id)
        if not poll or option_id not in {o["id"] for o in poll["options"]}:
            return "invalid"
        if (poll_id, voter_id) in self.votes:
            return "duplicate"
        self.votes[(poll_id, voter_id)] = option_id
        return "recorded"

    def ping(self):
        return self.healthy

    def close(self):
        return None


class FakeQueue:
    def __init__(self):
        self.main, self.processing, self.dead, self.healthy = [], [], [], True

    def push(self, message):
        import json

        self.main.insert(0, json.dumps(message))

    def pop(self, timeout):
        import json

        if not self.main:
            return None
        raw = self.main.pop()
        self.processing.insert(0, raw)
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            self.dead_letter(raw)
            return None

    def ack(self, raw):
        self.processing.remove(raw)

    def retry(self, raw, message):
        import json

        self.processing.remove(raw)
        self.main.insert(0, json.dumps(message))

    def dead_letter(self, raw):
        if raw in self.processing:
            self.processing.remove(raw)
        self.dead.insert(0, raw)

    def requeue_stale(self):
        n = len(self.processing)
        while self.processing:
            self.main.insert(0, self.processing.pop())
        return n

    def depth(self):
        return {"queued": len(self.main), "processing": len(self.processing), "dead": len(self.dead)}

    def ping(self):
        return self.healthy


class FakeCache:
    def __init__(self):
        self.data = {}

    def get(self, poll_id):
        return self.data.get(poll_id)

    def set(self, poll_id, results, ttl):
        self.data[poll_id] = results


@pytest.fixture
def settings():
    return Settings(
        app_version="9.9.9",
        database_url="postgresql://x",
        redis_url="redis://x",
        log_level="warn",
        log_format="text",
        cors_origins=(),
        max_options=5,
        max_question_length=100,
        results_cache_ttl=5,
        worker_metrics_port=0,
        worker_poll_seconds=0,
        worker_max_attempts=3,
    )


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def queue():
    return FakeQueue()


@pytest.fixture
def cache():
    return FakeCache()


@pytest.fixture
def client(settings, db, queue, cache):
    with TestClient(create_app(settings, db=db, queue=queue, cache=cache)) as c:
        yield c
