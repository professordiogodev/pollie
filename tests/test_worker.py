import json

import pytest

from pollie.worker.main import Worker


@pytest.fixture
def worker(settings, db, queue):
    w = Worker(settings, db, queue)
    w.stop.set()  # no sleeping in retry tests
    return w


def seed(db):
    poll = db.create_poll("q?", ["a", "b"])
    return poll["id"], poll["options"][0]["id"]


def message(poll_id, option_id, voter="v1", attempts=0):
    return {"poll_id": poll_id, "option_id": option_id, "voter_id": voter, "attempts": attempts}


def test_a_vote_is_recorded_and_acknowledged(worker, db, queue):
    pid, oid = seed(db)
    queue.push(message(pid, oid))
    assert worker.process_one() == "recorded"
    assert db.votes == {(pid, "v1"): oid} and queue.depth() == {"queued": 0, "processing": 0, "dead": 0}


def test_the_same_voter_twice_counts_once(worker, db, queue):
    pid, oid = seed(db)
    queue.push(message(pid, oid))
    queue.push(message(pid, oid))
    assert [worker.process_one(), worker.process_one()] == ["recorded", "duplicate"]
    assert len(db.votes) == 1


def test_an_empty_queue_returns_none(worker):
    assert worker.process_one() is None


def test_an_invalid_option_goes_to_the_dead_letter_queue(worker, db, queue):
    pid, _ = seed(db)
    queue.push(message(pid, 9999))
    assert worker.process_one() == "invalid" and queue.depth()["dead"] == 1


def test_a_malformed_message_is_dead_lettered(worker, queue):
    queue.main.insert(0, "{not json")
    assert worker.process_one() is None and queue.depth()["dead"] == 1
    queue.push({"poll_id": "x"})
    assert worker.process_one() == "invalid"


def test_a_database_outage_retries_then_dead_letters(worker, db, queue):
    pid, oid = seed(db)
    db.fail_votes = True
    queue.push(message(pid, oid))
    assert worker.process_one() == "retry"
    assert json.loads(queue.main[0])["attempts"] == 1
    assert worker.process_one() == "retry"
    assert worker.process_one() == "dead"
    assert queue.depth() == {"queued": 0, "processing": 0, "dead": 1}


def test_votes_are_recorded_once_the_database_is_back(worker, db, queue):
    pid, oid = seed(db)
    db.fail_votes = True
    queue.push(message(pid, oid))
    assert worker.process_one() == "retry"
    db.fail_votes = False
    assert worker.process_one() == "recorded"


def test_messages_left_by_a_crashed_worker_are_requeued(worker, queue):
    queue.processing.insert(0, json.dumps({"poll_id": 1}))
    assert queue.requeue_stale() == 1 and queue.depth()["queued"] == 1


def test_metrics_and_health(worker, db, queue):
    pid, oid = seed(db)
    queue.push(message(pid, oid))
    worker.process_one()
    text = worker.metrics_text().decode()
    assert 'pollie_worker_votes_total{result="recorded"} 1.0' in text and "pollie_worker_process_seconds_count 1.0" in text
    assert worker.healthy() is True
    worker.last_beat -= 3600
    assert worker.healthy() is False


def test_the_worker_survives_a_redis_outage_and_resumes(settings, db, queue):
    import threading
    import time

    pid, oid = seed(db)
    w = Worker(settings, db, queue)
    queue.push(message(pid, oid))
    queue.down = True
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    time.sleep(0.4)
    assert t.is_alive() and w.healthy()  # still alive while Redis is down
    assert (
        "pollie_worker_queue_errors_total 1.0" in w.metrics_text().decode()
        or "pollie_worker_queue_errors_total" in w.metrics_text().decode()
    )
    queue.down = False
    for _ in range(60):
        if db.votes:
            break
        time.sleep(0.1)
    w.stop.set()
    t.join(timeout=3)
    assert db.votes == {(pid, "v1"): oid}
