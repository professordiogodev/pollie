"""Run against real services:  DATABASE_URL=... REDIS_URL=... make test-integration"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from pollie.api.main import create_app
from pollie.common.config import Settings
from pollie.common.db import PostgresDatabase
from pollie.common.queue import RedisResultsCache, RedisVoteQueue
from pollie.worker.main import Worker

DATABASE_URL, REDIS_URL = os.environ.get("DATABASE_URL"), os.environ.get("REDIS_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (DATABASE_URL and REDIS_URL), reason="DATABASE_URL and REDIS_URL are not set"),
]


@pytest.fixture
def stack():
    settings = Settings.from_env()
    db, queue = PostgresDatabase(DATABASE_URL), RedisVoteQueue(REDIS_URL)
    db.init_schema()
    queue._r.delete("pollie:votes", "pollie:votes:processing", "pollie:votes:dead")
    app = create_app(settings, db=db, queue=queue, cache=RedisResultsCache(REDIS_URL))
    with TestClient(app) as client:
        yield client, Worker(settings, db, queue), queue


def test_a_vote_travels_from_api_through_redis_and_worker_to_postgres(stack):
    client, worker, queue = stack
    poll = client.post("/polls", json={"question": f"integration {uuid.uuid4().hex[:6]}?", "options": ["yes", "no"]}).json()
    option = poll["options"][0]["id"]
    assert client.post(f"/polls/{poll['id']}/votes", json={"option_id": option, "voter_id": "it-1"}).status_code == 202
    assert queue.depth()["queued"] == 1
    assert worker.process_one(timeout=1) == "recorded"
    assert queue.depth() == {"queued": 0, "processing": 0, "dead": 0}
    body = client.get(f"/polls/{poll['id']}/results").json()
    assert body["total_votes"] == 1 and body["results"][0]["votes"] == 1


def test_duplicate_votes_are_counted_once_in_postgres(stack):
    client, worker, _ = stack
    poll = client.post("/polls", json={"question": f"dupes {uuid.uuid4().hex[:6]}?", "options": ["a", "b"]}).json()
    for _ in range(2):
        client.post(f"/polls/{poll['id']}/votes", json={"option_id": poll["options"][0]["id"], "voter_id": "same"})
    assert [worker.process_one(timeout=1), worker.process_one(timeout=1)] == ["recorded", "duplicate"]


def test_a_stale_message_from_a_crashed_worker_is_picked_up(stack):
    client, worker, queue = stack
    poll = client.post("/polls", json={"question": f"crash {uuid.uuid4().hex[:6]}?", "options": ["a", "b"]}).json()
    client.post(f"/polls/{poll['id']}/votes", json={"option_id": poll["options"][0]["id"], "voter_id": "crash"})
    queue._r.blmove("pollie:votes", "pollie:votes:processing", 1, "RIGHT", "LEFT")  # a worker took it and died
    assert queue.depth() == {"queued": 0, "processing": 1, "dead": 0}
    assert queue.requeue_stale() == 1
    assert worker.process_one(timeout=1) == "recorded"


def test_readiness_reports_both_services(stack):
    client, _, _ = stack
    assert client.get("/readyz").json() == {"status": "ready", "checks": {"database": True, "redis": True}}
