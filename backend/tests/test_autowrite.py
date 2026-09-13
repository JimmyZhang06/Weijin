"""Durable orchestration, draft continuity and stop protection, with no paid calls."""

# ruff: noqa: F811
from sqlalchemy import select
from test_ai import configured, no_network, run_id  # noqa: F401
from test_story import client, create, post, state  # noqa: F401

from app import autowrite, openai_provider
from app.db import Session
from app.models import Draft, Job, ModelCall


def start(c, w, count=2):
    return post(
        c,
        "/branches/" + w["branch_id"] + "/autowrite",
        {
            "base_revision_id": state(c, w)["revision_id"],
            "text": "写一段重逢",
            "chapter_count": count,
        },
    )


def read(c, w):
    return c.get("/api/v1/branches/" + w["branch_id"] + "/autowrite").json()[0]


def step(rid):
    autowrite.advance()
    with Session() as s:
        jid = s.get(Job, rid).request["children"][-1]
    run_id(jid)


def fake(monkeypatch, callback=None):
    def generate(state, request, instruction, delta, usage, register, stopped):
        usage("test", {"input_tokens": 100, "output_tokens": 80})
        if callback:
            callback()
        if request["mode"] == "outline":
            return {
                "chapters": [
                    {"title": f"回信{i}", "summary": "以行动表达重逢后的迟疑。"}
                    for i in range(request["chapter_count"])
                ]
            }
        previous = request["previous_draft_chapters"]
        if previous:
            assert previous[-1]["body"] == "他放下信，又把它拾起。"
        return {"message": "请核对人物。", "title": f"回信{len(previous)}", "body": "他放下信，又把它拾起。"}

    monkeypatch.setattr(openai_provider, "generate", generate)


def test_full_draft_pause_resume_and_ordered_adoption(client, monkeypatch):
    configured(monkeypatch)
    fake(monkeypatch)
    w = create(client)
    rid = start(client, w).json()["id"]
    assert start(client, w).status_code == 429
    step(rid)
    assert len(read(client, w)["outline"]) == 2
    client.post(f"/api/v1/autowrite/{rid}/pause")
    autowrite.advance()
    with Session() as s:
        assert len(s.get(Job, rid).request["children"]) == 1
    client.post(f"/api/v1/autowrite/{rid}/resume")
    step(rid)
    # Simulate another coordinator invocation/restart: successful chapter is reused.
    step(rid)
    autowrite.advance()
    r = read(client, w)
    assert r["status"] == "succeeded" and len(r["chapters"]) == 2 and r["calls"] == 3
    assert r["usage"]["input_tokens"] == 300
    assert not state(client, w)["scenes"]
    assert client.get("/api/v1/branches/" + w["branch_id"] + "/conversation").json() == []
    assert post(client, f"/autowrite/{rid}/chapters/1/draft").status_code == 409
    for index in range(2):
        response = post(client, f"/autowrite/{rid}/chapters/{index}/draft")
        assert response.status_code == 200, response.text
        did = response.json()["draft_id"]
        assert post(client, f"/autowrite/{rid}/chapters/{index}/draft").json()["draft_id"] == did
        d = client.get("/api/v1/drafts/" + did).json()
        assert (
            post(
                client,
                "/drafts/" + did + "/accept",
                {"expected_revision_id": d["base_revision_id"], "acknowledge_semantic_review": True},
            ).status_code
            == 200
        )
    assert len(state(client, w)["scenes"]) == 2


def test_stop_during_call_keeps_usage_without_late_output(client, monkeypatch):
    configured(monkeypatch)
    w = create(client)
    rid = start(client, w).json()["id"]
    fake(monkeypatch, lambda: client.post(f"/api/v1/autowrite/{rid}/stop"))
    step(rid)
    autowrite.advance()
    r = read(client, w)
    assert r["status"] == "cancelled" and not r["outline"] and r["calls"] == 1
    assert r["usage"]["output_tokens"] == 80
    with Session() as s:
        child = s.get(Job, rid).request["children"][0]
        assert not s.scalar(select(Draft).where(Draft.job_id == child))


def test_failed_call_never_automatically_retried(client, monkeypatch):
    configured(monkeypatch)
    w = create(client)
    rid = start(client, w).json()["id"]

    def fail(*args):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(openai_provider, "generate", fail)
    step(rid)
    for _ in range(3):
        autowrite.advance()
    assert read(client, w)["status"] == "failed"
    with Session() as s:
        ids = s.get(Job, rid).request["children"]
        assert len(ids) == 1
        assert s.scalar(select(ModelCall).where(ModelCall.job_id == ids[0])).status == "outcome_unknown"


def test_configuration_and_foreign_owner(client, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    w = create(client)
    assert start(client, w).status_code == 503
    configured(monkeypatch)
    rid = start(client, w).json()["id"]
    from app.main import app, owner

    original = app.dependency_overrides[owner]
    app.dependency_overrides[owner] = lambda: "another-owner"
    try:
        assert client.post(f"/api/v1/autowrite/{rid}/stop").status_code == 404
        assert post(client, f"/autowrite/{rid}/chapters/0/draft").status_code == 404
    finally:
        app.dependency_overrides[owner] = original
        client.post(f"/api/v1/autowrite/{rid}/stop")


def test_revised_import_unlocks_next_chapter(client, monkeypatch):
    configured(monkeypatch)
    fake(monkeypatch)
    w = create(client)
    rid = start(client, w).json()["id"]
    for _ in range(3):
        step(rid)
    autowrite.advance()
    original = post(client, f"/autowrite/{rid}/chapters/0/draft").json()["draft_id"]
    assert post(client, f"/autowrite/{rid}/chapters/1/draft").status_code == 409
    # A distinct draft produced by a real revise request must carry the ancestry.
    response = post(
        client,
        f"/branches/{w['branch_id']}/conversation",
        {
            "base_revision_id": state(client, w)["revision_id"],
            "draft_id": original,
            "mode": "revise",
            "text": "修改措辞",
        },
    )
    assert response.status_code == 202, response.text
    monkeypatch.setattr(
        openai_provider,
        "generate",
        lambda *args: {"message": "已修改", "title": "修订第一章", "body": "他终于拆开了信。"},
    )
    run_id(response.json()["job_id"])
    with Session() as session:
        revised = session.scalar(select(Draft).where(Draft.job_id == response.json()["job_id"])).id
    assert post(client, f"/drafts/{original}/reject").status_code == 200
    assert (
        post(
            client,
            f"/drafts/{revised}/accept",
            {"expected_revision_id": state(client, w)["revision_id"], "acknowledge_semantic_review": True},
        ).status_code
        == 200
    )
    response = post(client, f"/autowrite/{rid}/chapters/1/draft")
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/drafts/" + response.json()["draft_id"]).json()["content"]["scene_number"] == 2

    reopened = post(client, f"/autowrite/{rid}/chapters/0/draft").json()
    assert reopened["draft_id"] == revised
    accepted = client.get("/api/v1/drafts/" + revised).json()
    assert accepted["accepted_scene_id"] == state(client, w)["scenes"][0]["id"]
    assert read(client, w)["import_statuses"][0] == "accepted"
