"""RedisVoteQueue, RedisResultsCache and the worker's HTTP endpoints, with fakes (no services)."""

import json
import threading
import urllib.request

import pytest

from pollie.common.queue import DEAD, MAIN, PROCESSING, RedisResultsCache, RedisVoteQueue
from pollie.worker.main import Worker, make_http_server


class FakeRedis:
    """Just enough of redis-py for the adapters."""

    def __init__(self):
        self.lists, self.kv, self.fail = {}, {}, False

    def _l(self, key):
        return self.lists.setdefault(key, [])

    def lpush(self, key, value):
        self._l(key).insert(0, value)

    def lrem(self, key, count, value):
        if value in self._l(key):
            self._l(key).remove(value)

    def llen(self, key):
        return len(self._l(key))

    def blmove(self, src, dst, timeout, where_src, where_dst):
        return self.lmove(src, dst, where_src, where_dst)

    def lmove(self, src, dst, where_src, where_dst):
        if not self._l(src):
            return None
        item = self._l(src).pop()
        self._l(dst).insert(0, item)
        return item

    def pipeline(self):
        outer = self

        class Pipe:
            def __init__(self):
                self.ops = []

            def lrem(self, *a):
                self.ops.append(("lrem", a))

            def lpush(self, *a):
                self.ops.append(("lpush", a))

            def execute(self):
                for name, a in self.ops:
                    getattr(outer, name)(*a)

        return Pipe()

    def get(self, key):
        if self.fail:
            raise ConnectionError("down")
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("down")
        self.kv[key] = value

    def ping(self):
        if self.fail:
            raise ConnectionError("down")
        return True


@pytest.fixture
def fake(monkeypatch):
    import redis

    r = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url, **kw: r))
    return r


def test_queue_roundtrip_ack_retry_and_dead_letter(fake):
    q = RedisVoteQueue("redis://x")
    q.push({"n": 1})
    raw, msg = q.pop(1)
    assert msg == {"n": 1} and q.depth() == {"queued": 0, "processing": 1, "dead": 0}
    q.retry(raw, {"n": 1, "attempts": 1})
    assert q.depth() == {"queued": 1, "processing": 0, "dead": 0}
    raw, _ = q.pop(1)
    q.dead_letter(raw)
    assert q.depth() == {"queued": 0, "processing": 0, "dead": 1}
    q.push({"n": 2})
    raw, _ = q.pop(1)
    q.ack(raw)
    assert q.depth()["processing"] == 0


def test_queue_pop_on_empty_and_on_garbage(fake):
    q = RedisVoteQueue("redis://x")
    assert q.pop(1) is None
    fake.lists.setdefault(MAIN, []).insert(0, "{broken")
    assert q.pop(1) is None and fake.lists[DEAD] == ["{broken"] and fake.lists.get(PROCESSING, []) == []


def test_queue_requeues_stale_messages(fake):
    q = RedisVoteQueue("redis://x")
    fake.lists[PROCESSING] = ['{"a":1}', '{"a":2}']
    assert q.requeue_stale() == 2 and q.depth()["queued"] == 2


def test_queue_ping(fake):
    q = RedisVoteQueue("redis://x")
    assert q.ping() is True
    fake.fail = True
    assert q.ping() is False


def test_results_cache_roundtrip_and_failure_is_a_miss(fake):
    c = RedisResultsCache("redis://x")
    assert c.get(1) is None
    c.set(1, [{"votes": 2}], 5)
    assert c.get(1) == [{"votes": 2}]
    fake.fail = True
    assert c.get(1) is None
    c.set(1, [], 5)  # must not raise


@pytest.fixture
def server(settings, db, queue):
    worker = Worker(settings, db, queue)
    srv = make_http_server(worker, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield worker, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_worker_http_endpoints(server, db):
    worker, base = server
    assert fetch(base + "/healthz")[0] == 200
    status, body = fetch(base + "/readyz")
    assert status == 200 and json.loads(body)["status"] == "ready"
    assert "pollie_worker_build_info" in fetch(base + "/metrics")[1]
    assert fetch(base + "/nope")[0] == 404
    db.healthy = False
    assert fetch(base + "/readyz")[0] == 503
    worker.last_beat -= 3600
    assert fetch(base + "/healthz")[0] == 503
