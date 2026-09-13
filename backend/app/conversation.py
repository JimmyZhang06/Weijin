"""Persist author conversations as jobs scoped to a branch and optional chapter."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import func, select

from .ai_config import public_status, settings
from .auth import owner
from .db import Session, uid
from .domain import Input, Problem, command, owned_branch, snapshot
from .models import Branch, Draft, Event, Job, Step, World
from .workspace import validate_plan

router = APIRouter(prefix="/api/v1")
Owner = Annotated[str, Depends(owner)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=100)]


class Message(Input):
    base_revision_id: UUID
    mode: Literal["discuss", "write", "revise"] = "discuss"
    text: str = Field(min_length=1, max_length=4000)
    scene_id: UUID | None = None
    draft_id: UUID | None = None
    plan_id: UUID | None = None
    selected_text: str = Field(default="", max_length=10000)


@router.get("/ai/status")
def ai_status(user: Owner):
    return public_status()


@router.post("/branches/{bid}/conversation", status_code=202)
def send(bid: UUID, data: Message, user: Owner, key: Key):
    with Session.begin() as s:
        owned_branch(s, str(bid), user)

        def execute():
            b = owned_branch(s, str(bid), user, lock=True)
            if b.head_revision_id != str(data.base_revision_id):
                raise Problem("REVISION_CONFLICT", "作品已经更新，请刷新后再发送。", 409)
            cfg = settings()
            if not public_status()["configured"]:
                raise Problem("AI_NOT_CONFIGURED", public_status()["message"], 503)
            s.execute(select(World).where(World.owner_id == user).order_by(World.id).with_for_update()).all()
            active = s.scalar(
                select(func.count())
                .select_from(Job)
                .join(Branch)
                .join(World)
                .where(
                    World.owner_id == user, Job.status.in_(["queued", "running", "orchestrating", "paused"])
                )
            )
            if active >= 2 or s.scalar(
                select(Job.id).where(
                    Job.branch_id == b.id, Job.status.in_(["queued", "running", "orchestrating", "paused"])
                )
            ):
                raise Problem("CONCURRENCY_LIMIT", "当前路线已有任务，或已达到两个并发任务上限。", 429)
            state = snapshot(s, b.head_revision_id)
            source, replacement = None, None
            if data.scene_id and data.draft_id:
                raise Problem("INVALID_TARGET", "一次只能引用一个章节或一份草稿。")
            if data.scene_id:
                source = next((x for x in state["scenes"] if x["id"] == str(data.scene_id)), None)
                if not source:
                    raise Problem("INVALID_TARGET", "章节不属于当前路线。")
                replacement = source["id"]
            if data.draft_id:
                draft = s.get(Draft, str(data.draft_id))
                job = s.get(Job, draft.job_id) if draft else None
                if (
                    not job
                    or job.branch_id != b.id
                    or job.base_revision_id != b.head_revision_id
                    or draft.status != "pending"
                ):
                    raise Problem("INVALID_TARGET", "请引用当前路线、当前版本的待定稿。", 409)
                if draft.content["kind"] != "scene":
                    raise Problem("INVALID_TARGET", "创作对话当前只支持正文稿件。")
                source = draft.content
                replacement = source.get("replace_scene_id")
            if data.mode == "revise" and not source:
                raise Problem("TARGET_REQUIRED", "请先打开需要修改的章节或草稿。")
            if data.mode == "write" and source:
                raise Problem("INVALID_TARGET", "续写新章请先选择未写章纲；修改当前章节请选择改稿。")
            if data.mode == "revise" and replacement and state["scenes"][-1]["id"] != replacement:
                raise Problem("BRANCH_REQUIRED", "较早章节之后已有后文，请先另开路线。", 409)
            selection = data.selected_text
            if selection and (not source or source["body"].count(selection) != 1):
                raise Problem("SELECTION_CHANGED", "选段不是唯一或正文已更新，请重新选择。", 409)
            plan_id = source.get("plan_id") if source else (str(data.plan_id) if data.plan_id else None)
            if data.mode != "discuss":
                validate_plan(state, plan_id, replacement)
            scope = plan_id or replacement or (str(data.draft_id) if data.draft_id else "work")
            # Only successful, same-scope and same-revision conversation is reused.
            history = []
            previous = s.scalars(
                select(Job)
                .where(
                    Job.branch_id == b.id,
                    Job.request["batch_id"].astext.is_(None),
                    Job.status == "succeeded",
                    Job.base_revision_id == b.head_revision_id,
                )
                .order_by(Job.created_at.desc())
                .limit(30)
            ).all()
            for old in reversed(previous):
                if old.request.get("kind") != "conversation" or old.request.get("scope") != scope:
                    continue
                result = s.scalar(select(Step).where(Step.job_id == old.id, Step.step == 2))
                if result:
                    history.extend(
                        [
                            {"role": "user", "content": old.request["text"]},
                            {"role": "assistant", "content": result.result["message"][:3000]},
                        ]
                    )
            payload = dict(
                data.model_dump(mode="json"),
                kind="conversation",
                provider=cfg["provider"],
                model=cfg["model"],
                max_output_tokens=cfg["max_output_tokens"],
                timeout=cfg["timeout"],
                prompt_version="novel-1",
                source_body=source["body"] if source else "",
                source_title=source["title"] if source else "",
                replace_scene_id=replacement,
                plan_id=plan_id,
                scope=scope,
                history=history[-8:],
            )
            from .openai_provider import build_input

            build_input(state, payload, b.instruction)  # Reject oversized context before paying.
            job = Job(id=uid(), branch_id=b.id, base_revision_id=b.head_revision_id, request=payload)
            s.add(job)
            s.flush()
            s.add(Event(job_id=job.id, kind="job.queued", payload={"provider": cfg["provider"]}))
            return {"job_id": job.id, "status": "queued"}

        return command(s, user, f"conversation:{bid}", key, data.model_dump(mode="json"), execute)


@router.get("/branches/{bid}/conversation")
def conversation(bid: UUID, user: Owner):
    with Session() as s:
        owned_branch(s, str(bid), user)
        rows = []
        jobs = s.scalars(
            select(Job)
            .where(
                Job.branch_id == str(bid),
                Job.request["kind"].astext == "conversation",
                Job.request["batch_id"].astext.is_(None),
            )
            .order_by(Job.created_at.desc())
            .limit(40)
        ).all()
        for j in reversed(jobs):
            step = s.scalar(select(Step).where(Step.job_id == j.id, Step.step == 2))
            draft = s.scalar(select(Draft).where(Draft.job_id == j.id))
            rows.append(
                {
                    "job_id": j.id,
                    "text": j.request["text"],
                    "mode": j.request["mode"],
                    "status": j.status,
                    "error": j.error,
                    "base_revision_id": j.base_revision_id,
                    "scope": j.request["scope"],
                    "reply": step.result["message"] if step else "",
                    "draft_id": draft.id if draft else None,
                    "draft_status": draft.status if draft else None,
                    "draft_preview": {
                        "title": draft.content["title"],
                        "body": draft.content["body"][:220]
                        + ("…" if len(draft.content["body"]) > 220 else ""),
                    }
                    if draft
                    else None,
                    "model": j.request["model"],
                }
            )
        return rows
