import pytest
from fastapi.testclient import TestClient

from pollie.api.main import create_app
from pollie.common.config import Settings


def test_healthz_ignores_dependencies(client, db, queue):
    db.healthy = queue.healthy = False
    assert client.get("/healthz").json()["status"] == "ok"


def test_readyz_reports_each_dependency(client, db, queue):
    assert client.get("/readyz").status_code == 200
    db.healthy = False
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"] == {"database": False, "redis": True}
    db.healthy, queue.healthy = True, False
    assert client.get("/readyz").json()["checks"]["redis"] is False


def test_metrics_expose_queue_depth_and_counters(client, queue):
    client.post("/polls", json={"question": "q?", "options": ["a", "b"]})
    client.post("/polls/1/votes", json={"option_id": 1, "voter_id": "v"})
    text = client.get("/metrics").text
    assert "pollie_polls_created_total 1.0" in text
    assert "pollie_votes_accepted_total 1.0" in text
    assert 'pollie_queue_depth{queue="queued"} 1.0' in text
    assert 'pollie_api_http_requests_total{method="POST",path="/polls",status="201"} 1.0' in text
    assert 'pollie_api_build_info{version="9.9.9"} 1.0' in text


def test_request_id_is_generated_and_echoed(client):
    assert len(client.get("/healthz").headers["x-request-id"]) >= 16
    assert client.get("/healthz", headers={"X-Request-ID": "abc"}).headers["x-request-id"] == "abc"


def test_unhandled_errors_are_clean_500s(settings, db, queue, cache):
    def boom():
        raise RuntimeError("boom")

    db.list_polls = lambda limit=50: boom()
    with TestClient(create_app(settings, db=db, queue=queue, cache=cache), raise_server_exceptions=False) as c:
        r = c.get("/polls")
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}


def test_settings_require_database_and_redis(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://x")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.from_env()
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.delenv("REDIS_URL")
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings.from_env()


def test_settings_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("REDIS_URL", "redis://x")
    monkeypatch.setenv("MAX_OPTIONS", "4")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    s = Settings.from_env()
    assert s.max_options == 4 and s.cors_origins == ("http://a.test", "http://b.test") and s.worker_metrics_port == 9100
    monkeypatch.setenv("MAX_OPTIONS", "many")
    with pytest.raises(ValueError, match="MAX_OPTIONS"):
        Settings.from_env()
