import pytest


def make_poll(client, question="Tabs or spaces?", options=("Tabs", "Spaces")):
    return client.post("/polls", json={"question": question, "options": list(options)})


def test_create_and_read_a_poll(client):
    r = make_poll(client)
    assert r.status_code == 201
    poll = r.json()
    assert poll["question"] == "Tabs or spaces?" and [o["label"] for o in poll["options"]] == ["Tabs", "Spaces"]
    assert client.get(f"/polls/{poll['id']}").json()["id"] == poll["id"]
    assert [p["id"] for p in client.get("/polls").json()] == [poll["id"]]


def test_unknown_poll_is_404(client):
    assert client.get("/polls/999").status_code == 404
    assert client.get("/polls/999/results").status_code == 404
    assert client.post("/polls/999/votes", json={"option_id": 1, "voter_id": "a"}).status_code == 404


@pytest.mark.parametrize(
    "question,options",
    [
        ("", ["a", "b"]),
        ("q", ["only one"]),
        ("q", ["a", "a"]),
        ("q", ["a", ""]),
        ("q", ["a", "b", "c", "d", "e", "f"]),
        ("x" * 101, ["a", "b"]),
    ],
)
def test_bad_polls_are_rejected(client, question, options):
    r = client.post("/polls", json={"question": question, "options": options})
    assert r.status_code == 422 and "detail" in r.json()


def test_voting_queues_the_vote_and_does_not_touch_the_database(client, db, queue):
    poll = make_poll(client).json()
    r = client.post(f"/polls/{poll['id']}/votes", json={"option_id": poll["options"][0]["id"], "voter_id": "v1"})
    assert r.status_code == 202 and r.json() == {"status": "queued"}
    assert queue.depth()["queued"] == 1 and db.votes == {}


def test_voting_for_an_option_of_another_poll_is_rejected(client):
    a, b = make_poll(client).json(), make_poll(client, "Dark or light?", ("Dark", "Light")).json()
    r = client.post(f"/polls/{a['id']}/votes", json={"option_id": b["options"][0]["id"], "voter_id": "v1"})
    assert r.status_code == 422


@pytest.mark.parametrize("voter", ["", "   ", "x" * 65])
def test_bad_voter_ids_are_rejected(client, voter):
    poll = make_poll(client).json()
    assert (
        client.post(f"/polls/{poll['id']}/votes", json={"option_id": poll["options"][0]["id"], "voter_id": voter}).status_code
        == 422
    )


def test_results_percentages_and_cache(client, db):
    poll = make_poll(client).json()
    a, b = (o["id"] for o in poll["options"])
    db.record_vote(poll["id"], a, "v1")
    db.record_vote(poll["id"], a, "v2")
    db.record_vote(poll["id"], b, "v3")
    first = client.get(f"/polls/{poll['id']}/results").json()
    assert first["total_votes"] == 3 and first["source"] == "database"
    assert [r["percent"] for r in first["results"]] == [66.7, 33.3]
    db.record_vote(poll["id"], b, "v4")  # not visible until the cache expires
    second = client.get(f"/polls/{poll['id']}/results").json()
    assert second["source"] == "cache" and second["total_votes"] == 3


def test_results_with_no_votes_are_zero_not_an_error(client):
    poll = make_poll(client).json()
    body = client.get(f"/polls/{poll['id']}/results").json()
    assert body["total_votes"] == 0 and all(r["percent"] == 0.0 for r in body["results"])
