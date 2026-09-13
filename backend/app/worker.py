import logging
import os
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select

from .db import Session, now, uid
from .domain import character_context, snapshot
from .mock import MockProvider
from .models import Branch, Draft, Event, Heartbeat, Job, Step
from .review import structure_review

STAGES = ["scene_plan", "character_contexts", "writing", "consistency_review"]


def claim():
    with Session.begin() as s:
        job = s.scalar(
            select(Job)
            .where(or_(Job.status == "queued", and_(Job.status == "running", Job.lease_until < now())))
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        job.status, job.token, job.lease_until = "running", job.token + 1, now() + timedelta(seconds=30)
        return job.id, job.token


def tick(job_id, token):
    # Mock calls are bounded and instantaneous. Future external calls require a separate heartbeat.
    with Session() as s:
        job = s.get(Job, job_id)
        if not job or job.status != "running" or job.token != token:
            return False
        state = snapshot(s, job.base_revision_id)
        request, step = dict(job.request), job.next_step
        instruction = s.get(Branch, job.branch_id).instruction
    if step == 0:
        result = {
            "provider": "mock",
            "prompt_version": "mock-2",
            "kind": request["kind"],
            "base_revision_id": job.base_revision_id,
            "plan_id": request.get("plan_id"),
            "planned_chapters": len(state.get("chapter_plans", [])),
            "brief_fields": [k for k, v in state.get("brief", {}).items() if v],
        }
    elif step == 1:
        # Persist only the successful validation count, never private contexts in events.
        for person in state["characters"]:
            character_context(state, person["id"], len(state["scenes"]))
        result = {"validated_characters": len(state["characters"])}
    elif step == 2:
        result = MockProvider().compose(state, request, instruction)
    else:
        with Session() as s:
            content = s.scalar(select(Step).where(Step.job_id == job_id, Step.step == 2)).result
        result = structure_review(content, state)
    with Session.begin() as s:
        job = s.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.status != "running" or job.token != token or job.lease_until <= now():
            return False
        if job.next_step != step:
            return True
        s.add(Step(job_id=job.id, step=step, result=result))
        job.next_step += 1
        job.lease_until = now() + timedelta(seconds=30)
        s.add(Event(job_id=job.id, kind="job.stage_changed", payload={"stage": STAGES[step]}))
        if step == len(STAGES) - 1:
            if not result["passed"]:
                job.status, job.error = "failed", "STRUCTURE_INVALID"
                s.add(Event(job_id=job.id, kind="job.failed", payload={"code": job.error}))
                return False
            content = s.scalar(select(Step).where(Step.job_id == job.id, Step.step == 2)).result
            draft = Draft(id=uid(), job_id=job.id, content=content)
            s.add(draft)
            job.status = "succeeded"
            s.add(Event(job_id=job.id, kind="draft.ready", payload={"draft_id": draft.id}))
            return False
    return True


def run_once():
    from .autowrite import advance

    advance()
    with Session.begin() as s:
        s.merge(Heartbeat(id="local-worker", updated_at=now()))
    lease = claim()
    if not lease:
        return False
    with Session() as s:
        is_conversation = s.get(Job, lease[0]).request.get("kind") == "conversation"
    if is_conversation:
        from .ai_worker import run

        run(*lease)
        return True
    try:
        while tick(*lease):
            time.sleep(float(os.getenv("MOCK_STEP_DELAY", "0.15")))
    except Exception:
        logging.exception("Mock job failed: %s", lease[0])
        with Session.begin() as s:
            job = s.scalar(select(Job).where(Job.id == lease[0]).with_for_update())
            if job.status == "running" and job.token == lease[1]:
                job.status, job.error = "failed", "MOCK_GENERATION_FAILED"
                s.add(Event(job_id=job.id, kind="job.failed", payload={"code": job.error}))
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            if not run_once():
                time.sleep(1)
        except Exception:
            logging.exception("Worker unavailable; retrying database connection")
            time.sleep(3)


if __name__ == "__main__":
    main()
