"""External calls have independent heartbeats and at-most-one dispatch per job."""

import threading
import time
from datetime import timedelta

from sqlalchemy import select

from . import deepseek_provider, openai_provider
from .db import Session, now, uid
from .domain import Problem, snapshot
from .models import Branch, Draft, Event, Heartbeat, Job, ModelCall, Step
from .review import structure_review


def run(job_id, token):
    with Session.begin() as s:
        j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if j.status != "running" or j.token != token or j.lease_until <= now():
            return
        prior = s.scalar(select(ModelCall).where(ModelCall.job_id == job_id))
        if prior:
            # A crashed/expired worker might have reached the provider. Never charge again silently.
            j.status, j.error = "failed", "CALL_OUTCOME_UNKNOWN"
            s.add(Event(job_id=j.id, kind="job.failed", payload={"code": j.error}))
            return
        request, state = dict(j.request), snapshot(s, j.base_revision_id)
        instruction = s.get(Branch, j.branch_id).instruction
        call = ModelCall(id=uid(), job_id=j.id, model=request["model"], status="started")
        s.add(call)
        call_id = call.id
        s.add(
            Step(
                job_id=j.id,
                step=0,
                result={
                    "provider": request.get("provider", "openai"),
                    "model": request["model"],
                    "prompt_version": request["prompt_version"],
                },
            )
        )
        s.add(
            Step(
                job_id=j.id,
                step=1,
                result={
                    "validated_characters": len(state["characters"]),
                    "private_knowledge_included": False,
                    "recent_chapters": min(3, len(state["scenes"])),
                },
            )
        )
        j.next_step = 2
        s.add(
            Event(
                job_id=j.id, kind="job.stage_changed", payload={"stage": "writing", "mode": request["mode"]}
            )
        )
    done, interrupted = threading.Event(), threading.Event()
    client_holder = []
    deadline = time.monotonic() + request["timeout"]

    def heartbeat():
        while not done.wait(2):
            try:
                with Session.begin() as s:
                    j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
                    if (
                        j.status != "running"
                        or j.token != token
                        or j.lease_until <= now()
                        or time.monotonic() >= deadline
                    ):
                        interrupted.set()
                    else:
                        j.lease_until = now() + timedelta(seconds=30)
                        s.merge(Heartbeat(id="local-worker", updated_at=now()))
            except Exception:
                interrupted.set()
            if interrupted.is_set():
                if client_holder:
                    client_holder[0].close()
                return

    def emit(delta):
        with Session.begin() as s:
            j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if j.status != "running" or j.token != token or j.lease_until <= now():
                raise Problem("AI_INTERRUPTED", "任务已停止。")
            s.add(Event(job_id=job_id, kind="assistant.delta", payload={"text": delta}))

    def usage(response_id, counts):
        # Account for an issued call even if cancellation prevents its story output being committed.
        with Session.begin() as s:
            call = s.get(ModelCall, call_id)
            call.response_id, call.usage, call.status = response_id, counts, "response_received"

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        provider = deepseek_provider if request.get("provider") == "deepseek" else openai_provider
        result = provider.generate(
            state, request, instruction, emit, usage, client_holder.append, interrupted.is_set
        )
        content = None
        if request["mode"] not in {"discuss", "outline"}:
            content = {
                "kind": "scene",
                "title": result["title"],
                "body": result["body"],
                "mock": False,
                "source": request.get("provider", "openai"),
                "scene_number": len(state["scenes"]) + (0 if request.get("replace_scene_id") else 1),
                "note": "AI 生成提案；未经自动语义审校，采用前请核对人物与新增事实。",
            }
            for name in ("plan_id", "replace_scene_id"):
                if request.get(name):
                    content[name] = request[name]
        report = (
            structure_review(content, state)
            if content
            else {"passed": True, "scope": "conversation_only", "checks": [], "notice": "讨论未写入正文。"}
        )
        if not report["passed"]:
            raise Problem("STRUCTURE_INVALID", "模型稿件结构校验失败。")
        with Session.begin() as s:
            j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if j.status != "running" or j.token != token or j.lease_until <= now() or interrupted.is_set():
                if j.status == "running" and j.token == token:
                    j.status, j.error = "failed", "AI_INTERRUPTED"
                    s.add(Event(job_id=job_id, kind="job.failed", payload={"code": j.error}))
                return
            s.add(Step(job_id=job_id, step=2, result=result))
            s.add(Step(job_id=job_id, step=3, result=report))
            if content and not request.get("batch_id"):
                d = Draft(id=uid(), job_id=job_id, content=content)
                s.add(d)
                s.add(Event(job_id=job_id, kind="draft.ready", payload={"draft_id": d.id}))
            j.status, j.next_step = "succeeded", 4
            s.add(Event(job_id=job_id, kind="assistant.completed", payload={"has_draft": bool(content)}))
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, Problem)
            else (
                "DEEPSEEK_REQUEST_FAILED"
                if request.get("provider") == "deepseek"
                else "OPENAI_REQUEST_FAILED"
            )
        )
        # Never log exception response bodies: they may contain work content or sensitive headers.
        with Session.begin() as s:
            j = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if j.status == "running" and j.token == token:
                j.status, j.error = "failed", code
                s.add(Event(job_id=job_id, kind="job.failed", payload={"code": code}))
            call = s.get(ModelCall, call_id)
            if call.status == "started":
                call.status = "outcome_unknown"
    finally:
        done.set()
        thread.join(timeout=3)
