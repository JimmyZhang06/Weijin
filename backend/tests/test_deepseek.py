# ruff: noqa: F811
import json
from types import SimpleNamespace as NS

import pytest
from test_ai import run_id
from test_autowrite import read, step
from test_story import client, create, post, state  # noqa: F401

from app import autowrite, deepseek_provider
from app.domain import Problem


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(deepseek_provider, "OpenAI", lambda **kw: pytest.fail("No network in tests"))


def test_stream_json_and_usage(monkeypatch):
    args = {}
    usage = []

    class Stream:
        def __enter__(self):
            return iter(
                [
                    NS(
                        id="r",
                        usage=None,
                        choices=[
                            NS(
                                finish_reason=None,
                                delta=NS(
                                    content='{"message":"说明","title":"回信","body":"新选段"}',
                                    reasoning_content="private",
                                ),
                            )
                        ],
                    ),
                    NS(
                        id="r",
                        usage=NS(model_dump=lambda: {"prompt_tokens": 42, "completion_tokens": 18}),
                        choices=[NS(finish_reason="stop", delta=NS(content=None))],
                    ),
                ]
            )

        def __exit__(self, *a):
            pass

    def call(**kw):
        args.update(kw)
        return Stream()

    def factory(**kw):
        assert kw["base_url"] == "https://api.deepseek.com" and kw["max_retries"] == 0
        return NS(chat=NS(completions=NS(create=call)), close=lambda: None)

    monkeypatch.setattr(deepseek_provider, "OpenAI", factory)
    request = dict(
        mode="revise",
        model="deepseek-v4-flash",
        timeout=20,
        max_output_tokens=1000,
        text="改选段",
        source_body="前旧选段后",
        source_title="原题",
        selected_text="旧选段",
    )
    result = deepseek_provider.generate(
        dict(opening="", characters=[], facts=[], scenes=[]),
        request,
        "",
        lambda x: None,
        lambda rid, u: usage.append(u),
        lambda c: None,
        lambda: False,
    )
    assert result["body"] == "前新选段后" and result["title"] == "原题"
    assert usage[0]["input_tokens"] == 42
    assert args["response_format"] == {"type": "json_object"}
    assert args["extra_body"]["thinking"]["type"] == "disabled"


def test_deepseek_worker_and_autowrite(client, monkeypatch):
    def response(state, req, instruction, delta, usage, register, stopped):
        usage("r", {"input_tokens": 5, "output_tokens": 10})
        if req["mode"] == "outline":
            return {"chapters": [{"title": "回信", "summary": "读信"}]}
        return {"message": "请审阅", "title": "回信", "body": "他拿起信纸。"}

    monkeypatch.setattr(deepseek_provider, "generate", response)
    w = create(client)
    config = client.get("/api/v1/ai/status").json()
    assert config["provider"] == "deepseek" and "test-only-key" not in json.dumps(config)
    jid = post(
        client,
        f"/branches/{w['branch_id']}/conversation",
        {"base_revision_id": state(client, w)["revision_id"], "mode": "write", "text": "写一章"},
    ).json()["job_id"]
    run_id(jid)
    j = client.get("/api/v1/jobs/" + jid).json()
    d = client.get("/api/v1/drafts/" + j["draft_id"]).json()
    assert d["content"]["source"] == "deepseek"
    assert (
        post(
            client, "/drafts/" + d["id"] + "/accept", {"expected_revision_id": d["base_revision_id"]}
        ).status_code
        == 422
    )
    rid = post(
        client,
        f"/branches/{w['branch_id']}/autowrite",
        {"base_revision_id": state(client, w)["revision_id"], "text": "再写一章", "chapter_count": 1},
    ).json()["id"]
    step(rid)
    step(rid)
    autowrite.advance()
    assert read(client, w)["status"] == "succeeded"


@pytest.mark.parametrize("output", ["[]", "{}", '{"chapters":null}', "not json"])
def test_invalid_outline_rejected(output):
    with pytest.raises(Problem):
        deepseek_provider.parse_output(output, {"mode": "outline", "chapter_count": 2})
