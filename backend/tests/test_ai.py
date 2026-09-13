"""No paid requests: adapter and task runner are replaced with deterministic fixtures."""
# ruff: noqa: F811 -- pytest discovers the explicitly imported shared client fixture.

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_story import accept, client, create, generate, post, state  # noqa: F401

from app import ai_worker, openai_provider
from app.db import Session, now, uid
from app.domain import Problem
from app.models import Draft, Job, ModelCall


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(**kwargs):
        raise AssertionError("Live OpenAI requests are prohibited in tests")

    monkeypatch.setattr(openai_provider, "OpenAI", blocked)


def configured(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")


def send(client, w, **extra):
    return post(
        client,
        "/branches/" + w["branch_id"] + "/conversation",
        {"base_revision_id": state(client, w)["revision_id"], "text": "写一个克制的重逢", **extra},
    )


def run_id(jid):
    with Session.begin() as s:
        job = s.get(Job, jid)
        job.status, job.token, job.lease_until = "running", 1, now() + timedelta(seconds=30)
    ai_worker.run(jid, 1)


def fake_output(monkeypatch, callback=None):
    def response(state, request, instruction, delta, usage, register, stopped):
        usage("response-test", {"input_tokens": 123, "output_tokens": 64})
        if callback:
            callback()
        if request["mode"] == "discuss":
            delta("建议通过动作表现迟疑。")
        return {"message": "建议通过动作表现迟疑。", "title": "回信", "body": "他把信放下，又重新拿起。"}

    monkeypatch.setattr(openai_provider, "generate", response)


def test_config_and_discussion_persistence(client, monkeypatch):
    w = create(client)
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    assert send(client, w).status_code == 503
    configured(monkeypatch)
    status = client.get("/api/v1/ai/status").json()
    assert status["configured"] and "fake-test-key" not in json.dumps(status)
    fake_output(monkeypatch)
    result = send(client, w)
    assert result.status_code == 202, result.text
    jid = result.json()["job_id"]
    run_id(jid)
    records = client.get("/api/v1/branches/" + w["branch_id"] + "/conversation").json()
    assert records[-1]["reply"] == "建议通过动作表现迟疑。"
    assert records[-1]["draft_id"] is None
    assert not state(client, w)["scenes"]
    trace = client.get("/api/v1/jobs/" + jid + "/trace").json()
    assert trace["cost"] is None and trace["usage"]["input_tokens"] == 123
    assert "迟疑" not in json.dumps(trace, ensure_ascii=False)
    followup = send(client, w, text="这个建议如何落实？").json()
    with Session() as s:
        assert len(s.get(Job, followup["job_id"]).request["history"]) == 2
    client.post("/api/v1/jobs/" + followup["job_id"] + "/cancel")


def test_ai_draft_review_and_version_protection(client, monkeypatch):
    configured(monkeypatch)
    w = create(client)
    fake_output(monkeypatch)
    result = send(client, w, mode="write")
    run_id(result.json()["job_id"])
    job = client.get("/api/v1/jobs/" + result.json()["job_id"]).json()
    d = client.get("/api/v1/drafts/" + job["draft_id"]).json()
    assert accept(client, d).status_code == 422
    assert (
        post(
            client,
            "/drafts/" + d["id"] + "/accept",
            {"expected_revision_id": d["base_revision_id"], "acknowledge_semantic_review": True},
        ).status_code
        == 200
    )
    original = state(client, w)["scenes"][-1]
    revised = send(client, w, mode="revise", scene_id=original["id"])
    run_id(revised.json()["job_id"])
    jid = revised.json()["job_id"]
    did = client.get("/api/v1/jobs/" + jid).json()["draft_id"]
    proposal = client.get("/api/v1/drafts/" + did).json()
    assert proposal["comparison"]["before"] == original["body"]
    assert state(client, w)["scenes"][-1]["id"] == original["id"]


def test_cancel_records_usage_without_draft(client, monkeypatch):
    configured(monkeypatch)
    w = create(client)
    jid = send(client, w, mode="write").json()["job_id"]
    fake_output(monkeypatch, lambda: client.post("/api/v1/jobs/" + jid + "/cancel"))
    run_id(jid)
    with Session() as s:
        assert s.get(Job, jid).status == "cancelled"
        assert not s.scalar(select(Draft).where(Draft.job_id == jid))
        assert s.scalar(select(ModelCall).where(ModelCall.job_id == jid)).usage["output_tokens"] == 64


def test_uncertain_call_never_reissued(client, monkeypatch):
    configured(monkeypatch)
    w = create(client)
    jid = send(client, w).json()["job_id"]
    with Session.begin() as s:
        s.add(ModelCall(id=uid(), job_id=jid, model="test", status="started"))
    run_id(jid)
    with Session() as s:
        assert s.get(Job, jid).error == "CALL_OUTCOME_UNKNOWN"


def test_context_is_bounded_and_private_knowledge_omitted(client, monkeypatch):
    configured(monkeypatch)
    w = create(client)
    result = send(client, w)
    with Session() as s:
        job = s.get(Job, result.json()["job_id"])
        from app.domain import snapshot

        inputs = openai_provider.build_input(snapshot(s, job.base_revision_id), job.request, "")
        assert "只有甲知道的秘密" not in json.dumps(inputs, ensure_ascii=False)
        request = dict(job.request, source_body="字" * 61000)
        with pytest.raises(Problem):
            openai_provider.build_input(snapshot(s, job.base_revision_id), request, "")
    client.post("/api/v1/jobs/" + result.json()["job_id"] + "/cancel")
    outsider = create(client)
    draft = generate(client, outsider)
    assert send(client, w, mode="revise", draft_id=draft["id"]).status_code == 409


def test_adapter_stream_selection_and_incomplete(monkeypatch):
    configured(monkeypatch)
    seen, usage, output = (
        {},
        [],
        json.dumps({"message": "已改写", "title": "无关标题", "body": "他慢慢放下信。"}, ensure_ascii=False),
    )
    complete = True

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            yield SimpleNamespace(type="response.output_text.delta", delta=output)
            yield SimpleNamespace(
                type="response.completed" if complete else "response.incomplete",
                response=SimpleNamespace(
                    id="resp",
                    usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 10, "output_tokens": 20}),
                ),
            )

    class Client:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.responses = self

        def create(self, **kwargs):
            seen.update(kwargs)
            return Stream()

        def close(self):
            pass

    monkeypatch.setattr(openai_provider, "OpenAI", Client)
    request = {
        "mode": "revise",
        "text": "修改选段",
        "model": "configurable-model",
        "timeout": 30,
        "max_output_tokens": 900,
        "source_body": "开头。他很难过。结尾。",
        "selected_text": "他很难过。",
        "source_title": "回信",
    }
    state = {"opening": "重逢", "characters": [], "facts": [], "scenes": []}
    result = openai_provider.generate(
        state, request, "", lambda x: None, lambda *x: usage.append(x), lambda x: None, lambda: False
    )
    assert result["body"] == "开头。他慢慢放下信。结尾。" and result["title"] == "回信"
    assert seen["model"] == "configurable-model" and seen["max_retries"] == 0 and seen["store"] is False
    assert usage[0][1]["output_tokens"] == 20
    complete = False
    with pytest.raises(Problem, match="回复未完整"):
        openai_provider.generate(
            state, request, "", lambda x: None, lambda *x: None, lambda x: None, lambda: False
        )
