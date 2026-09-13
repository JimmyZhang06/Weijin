"""Real PostgreSQL integration tests; own test worlds only, no destructive resets."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db import Session, now
from app.domain import character_context, digest, snapshot
from app.main import app, owner
from app.models import Draft, Job, Revision
from app.worker import claim, run_once, tick


@pytest.fixture
def client():
    identity = "test-" + str(uuid4())
    app.dependency_overrides[owner] = lambda: identity
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def post(c, path, data=None, key=None):
    return c.post("/api/v1" + path, json=data, headers={"Idempotency-Key": key or str(uuid4())})


def create(c):
    r = post(
        c,
        "/worlds",
        {
            "title": "测试故事",
            "opening": "车站重逢",
            "characters": [
                {"name": "甲", "trait": "内向", "secret": "只有甲知道的秘密"},
                {"name": "乙", "trait": "谨慎", "secret": "只有乙知道的秘密"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def state(c, w):
    return c.get("/api/v1/branches/" + w["branch_id"] + "/state").json()


def generate(c, w, kind="scene", **extra):
    current = state(c, w)
    r = post(
        c,
        "/branches/" + w["branch_id"] + "/jobs",
        {"kind": kind, "base_revision_id": current["revision_id"], **extra},
    )
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    # Drain queue, including previous test jobs, with bounded work.
    for _ in range(30):
        run_once()
        j = c.get("/api/v1/jobs/" + jid).json()
        if j["status"] == "succeeded":
            return c.get("/api/v1/drafts/" + j["draft_id"]).json()
    pytest.fail("job did not complete")


def accept(c, d):
    return post(c, "/drafts/" + d["id"] + "/accept", {"expected_revision_id": d["base_revision_id"]})


def test_draft_accept_reveal_and_fork(client):
    w = create(client)
    d = generate(client, w)
    assert not state(client, w)["scenes"]
    assert accept(client, d).status_code == 200
    current = state(client, w)
    original_scene = current["scenes"][0]
    a = generate(
        client,
        w,
        "private_artifact",
        scene_id=original_scene["id"],
        character_id=current["characters"][0]["id"],
    )
    assert accept(client, a).status_code == 200
    current = state(client, w)
    artifact = current["artifacts"][0]
    assert "body" not in artifact
    path = "/branches/" + w["branch_id"] + "/artifacts/" + artifact["id"] + "/reveal"
    assert post(client, path).status_code == 422
    client.put("/api/v1/branches/" + w["branch_id"] + "/reading-progress", json={"scene_count": 1})
    r1, r2 = post(client, path), post(client, path)
    assert r1.status_code == 200 and r1.json() == r2.json()
    assert state(client, w)["revision_id"] == current["revision_id"]
    fork = post(
        client,
        "/branches/" + w["branch_id"] + "/forks",
        {
            "expected_revision_id": current["revision_id"],
            "before_scene_id": original_scene["id"],
            "title": "回头",
            "instruction": "她回头",
        },
    )
    assert fork.status_code == 201, fork.text
    new = fork.json()
    assert not state(client, new)["scenes"] and not state(client, new)["artifacts"]
    assert state(client, w)["scenes"][0] == original_scene


def test_context_isolation_and_time(client):
    w = create(client)
    with Session() as s:
        data = snapshot(s, w["revision_id"])
    cid = data["characters"][0]["id"]
    context = character_context(data, cid, 0)
    assert "只有乙知道的秘密" not in str(context)
    assert "只有甲知道的秘密" in str(context)
    data["knowledge"].append({"character_id": cid, "text": "未来消息", "scene": 3})
    assert "未来消息" not in str(character_context(data, cid, 2))
    assert "facts" not in state(client, w)


def test_owner_boundary(client):
    w = create(client)
    app.dependency_overrides[owner] = lambda: "someone-else"
    r = client.get("/api/v1/branches/" + w["branch_id"] + "/state")
    assert r.status_code == 404


def test_idempotency_and_conflict(client):
    w = create(client)
    key = str(uuid4())
    payload = {"kind": "scene", "base_revision_id": w["revision_id"]}
    path = "/branches/" + w["branch_id"] + "/jobs"
    a, b = post(client, path, payload, key), post(client, path, payload, key)
    assert a.json() == b.json()
    payload["base_revision_id"] = str(uuid4())
    assert post(client, path, payload, key).status_code == 409
    post(client, "/jobs/" + a.json()["job_id"] + "/cancel")


def test_concurrent_accepts_only_one_wins(client):
    w = create(client)
    a, b = generate(client, w), generate(client, w)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda d: accept(client, d).status_code, [a, b]))
    assert sorted(results) == [200, 409]
    assert len(state(client, w)["scenes"]) == 1


def test_lease_fencing_resume_and_cancel(client):
    w = create(client)
    r = post(
        client,
        "/branches/" + w["branch_id"] + "/jobs",
        {"kind": "scene", "base_revision_id": w["revision_id"]},
    )
    jid = r.json()["job_id"]
    lease = claim()
    assert lease[0] == jid
    assert tick(*lease)
    with Session.begin() as s:
        s.execute(update(Job).where(Job.id == jid).values(lease_until=now() - timedelta(seconds=1)))
    newer = claim()
    assert newer[1] > lease[1]
    assert tick(*lease) is False
    while tick(*newer):
        pass
    with Session() as s:
        assert s.scalar(select(Draft).where(Draft.job_id == jid)) is not None
    r = post(
        client,
        "/branches/" + w["branch_id"] + "/jobs",
        {"kind": "scene", "base_revision_id": w["revision_id"]},
    )
    lease = claim()
    post(client, "/jobs/" + r.json()["job_id"] + "/cancel")
    assert tick(*lease) is False


def test_revision_database_guard_and_hash(client):
    w = create(client)
    with Session() as s:
        rev = s.get(Revision, w["revision_id"])
        assert rev.state_hash == digest(rev.state)
    with pytest.raises(Exception, match="immutable"):
        with Session.begin() as s:
            s.execute(update(Revision).where(Revision.id == w["revision_id"]).values(kind="tampered"))


def test_sse_replay_and_input_validation(client):
    w = create(client)
    d = generate(client, w)
    with Session() as s:
        jid = s.get(Draft, d["id"]).job_id
    response = client.get("/api/v1/jobs/" + jid + "/events")
    assert "draft.ready" in response.text and "body" not in response.text
    last = int([line[4:] for line in response.text.splitlines() if line.startswith("id: ")][-1])
    assert not client.get("/api/v1/jobs/" + jid + "/events", headers={"Last-Event-ID": str(last)}).text
    assert (
        post(
            client,
            "/branches/" + w["branch_id"] + "/jobs",
            {"kind": "scene", "base_revision_id": w["revision_id"], "secret_override": True},
        ).status_code
        == 422
    )


def test_origin_rejected(client):
    assert client.get("/api/v1/worlds", headers={"Origin": "https://evil.example"}).status_code == 403


def workspace_payload(c, w):
    current = state(c, w)
    return {
        "expected_revision_id": current["revision_id"],
        "brief": {
            "work_type": "fanfic",
            "fandom": "自建测试原作",
            "canon_anchor": "原作终章之后",
            "divergence": "如果错过的信被收到",
            "boundaries": "不能让甲提前知道乙的秘密",
        },
        "chapter_plans": [
            {"id": str(uuid4()), "title": "重逢", "summary": "揭示双方误解", "target_words": 1200}
        ],
        "characters": current["characters"],
    }


def test_workspace_canon_plan_lock_and_stale_revision(client):
    w = create(client)
    data = workspace_payload(client, w)
    data["characters"][0]["trait"] = "新的核心特点"
    path = "/api/v1/branches/" + w["branch_id"] + "/workspace"
    assert client.put(path, json=data, headers={"Idempotency-Key": str(uuid4())}).status_code == 422
    data["allow_locked_changes"] = True
    r = client.put(path, json=data, headers={"Idempotency-Key": str(uuid4())})
    assert r.status_code == 200, r.text
    current = state(client, w)
    assert current["brief"]["fandom"] == "自建测试原作"
    assert current["chapter_plans"][0]["title"] == "重逢"
    assert client.put(path, json=data, headers={"Idempotency-Key": str(uuid4())}).status_code == 409
    with Session() as s:
        assert snapshot(s, w["revision_id"])["characters"][0]["trait"] == "内向"


def test_manual_draft_edit_hash_and_history(client):
    w = create(client)
    response = post(
        client,
        "/branches/" + w["branch_id"] + "/manual-drafts",
        {"base_revision_id": w["revision_id"], "title": "第一章", "body": "作者自己的正文。"},
    )
    assert response.status_code == 201, response.text
    did = response.json()["draft_id"]
    original = client.get("/api/v1/drafts/" + did).json()
    payload = {"expected_version": 1, "title": "第一章 · 修订", "body": "作者修改过的正文。"}
    assert (
        client.patch(
            "/api/v1/drafts/" + did, json=payload, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/v1/drafts/" + did, json=payload, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 409
    )
    stale = post(
        client,
        "/drafts/" + did + "/accept",
        {
            "expected_revision_id": w["revision_id"],
            "expected_content_hash": original["content_hash"],
            "acknowledge_semantic_review": True,
        },
    )
    assert stale.status_code == 409
    edited = client.get("/api/v1/drafts/" + did).json()
    assert not state(client, w)["scenes"]
    no_ack = post(
        client,
        "/drafts/" + did + "/accept",
        {"expected_revision_id": w["revision_id"], "expected_content_hash": edited["content_hash"]},
    )
    assert no_ack.status_code == 422
    accepted = post(
        client,
        "/drafts/" + did + "/accept",
        {
            "expected_revision_id": w["revision_id"],
            "expected_content_hash": edited["content_hash"],
            "acknowledge_semantic_review": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert state(client, w)["scenes"][0]["body"] == "作者修改过的正文。"
    history = client.get("/api/v1/branches/" + w["branch_id"] + "/history").json()
    assert [r["kind"] for r in history] == ["scene", "initial"]


def test_revision_marks_artifacts_stale(client):
    w = create(client)
    assert accept(client, generate(client, w)).status_code == 200
    current = state(client, w)
    scene = current["scenes"][0]
    artifact = generate(
        client, w, "private_artifact", scene_id=scene["id"], character_id=current["characters"][0]["id"]
    )
    assert accept(client, artifact).status_code == 200
    current = state(client, w)
    response = post(
        client,
        "/branches/" + w["branch_id"] + "/manual-drafts",
        {
            "base_revision_id": current["revision_id"],
            "replace_scene_id": scene["id"],
            "title": "新章名",
            "body": "修改后的场景。",
        },
    )
    assert response.status_code == 201
    draft = client.get("/api/v1/drafts/" + response.json()["draft_id"]).json()
    assert (
        post(
            client,
            "/drafts/" + draft["id"] + "/accept",
            {
                "expected_revision_id": draft["base_revision_id"],
                "expected_content_hash": draft["content_hash"],
                "acknowledge_semantic_review": True,
            },
        ).status_code
        == 200
    )
    changed = state(client, w)
    assert len(changed["scenes"]) == 1
    assert changed["artifacts"][0]["stale"] is True
    aid = changed["artifacts"][0]["id"]
    assert post(client, "/branches/" + w["branch_id"] + "/artifacts/" + aid + "/reveal").status_code == 409


def test_plan_binding_trace_and_early_edit(client):
    w = create(client)
    data = workspace_payload(client, w)
    assert (
        client.put(
            "/api/v1/branches/" + w["branch_id"] + "/workspace",
            json=data,
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 200
    )
    draft = generate(client, w, plan_id=data["chapter_plans"][0]["id"])
    assert draft["content"]["title"] == "重逢"
    trace = client.get("/api/v1/jobs/" + draft["job_id"] + "/trace").json()
    assert len(trace["steps"]) == 4
    assert trace["steps"][-1]["result"]["semantic_review"] == "not_performed"
    assert "只有甲知道的秘密" not in str(trace)
    assert accept(client, draft).status_code == 200
    first = state(client, w)["scenes"][0]
    assert accept(client, generate(client, w)).status_code == 200
    r = post(
        client,
        "/branches/" + w["branch_id"] + "/manual-drafts",
        {
            "base_revision_id": state(client, w)["revision_id"],
            "replace_scene_id": first["id"],
            "title": "重写第一章",
            "body": "有后文时必须分支。",
        },
    )
    assert r.status_code == 409


def test_new_workspace_endpoints_owner_boundary(client):
    w = create(client)
    d = generate(client, w)
    data = workspace_payload(client, w)
    app.dependency_overrides[owner] = lambda: "intruder"
    assert client.get("/api/v1/branches/" + w["branch_id"] + "/history").status_code == 404
    assert client.get("/api/v1/jobs/" + d["job_id"] + "/trace").status_code == 404
    assert (
        client.patch(
            "/api/v1/drafts/" + d["id"],
            json={"expected_version": 1, "title": "x", "body": "x"},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/v1/branches/" + w["branch_id"] + "/workspace",
            json=data,
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 404
    )
