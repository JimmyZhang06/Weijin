from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import Field
from sqlalchemy import select

from .auth import owner
from .db import Session, uid
from .domain import Input, Problem, StoryBrief, command, commit_revision, digest, owned_branch, snapshot
from .models import Draft, Event, Job, Revision, Step

router = APIRouter(prefix="/api/v1")
Owner = Annotated[str, Depends(owner)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=100)]


class ChapterPlan(Input):
    id: UUID
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=3000)
    target_words: int = Field(default=1500, ge=100, le=20000)


class PersonEdit(Input):
    id: UUID
    name: str = Field(min_length=1, max_length=40)
    trait: str = Field(min_length=1, max_length=400)
    desire: str = Field(default="", max_length=400)


class WorkspaceEdit(Input):
    expected_revision_id: UUID
    brief: StoryBrief
    chapter_plans: list[ChapterPlan] = Field(max_length=100)
    characters: list[PersonEdit] = Field(min_length=2, max_length=5)
    allow_locked_changes: bool = False


@router.put("/branches/{bid}/workspace")
def save_workspace(bid: UUID, data: WorkspaceEdit, user: Owner, key: Key):
    payload = data.model_dump(mode="json")
    with Session.begin() as s:
        owned_branch(s, str(bid), user)

        def execute():
            b = owned_branch(s, str(bid), user, lock=True)
            if b.head_revision_id != str(data.expected_revision_id):
                raise Problem("REVISION_CONFLICT", "设定已在别处更新，请刷新后再保存。", 409)
            current = snapshot(s, b.head_revision_id)
            ids = [p["id"] for p in payload["chapter_plans"]]
            if len(ids) != len(set(ids)):
                raise Problem("DUPLICATE_PLAN", "章节标识不能重复。")
            attached = {x.get("plan_id") for x in current["scenes"]} - {None}
            if not attached.issubset(set(ids)):
                raise Problem("PLAN_IN_USE", "已定稿章节的章纲不能删除。")
            before = {c["id"]: c for c in current["characters"]}
            after = {c["id"]: c for c in payload["characters"]}
            if before.keys() != after.keys() or len(after) != len(data.characters):
                raise Problem("CHARACTER_MISMATCH", "本次只支持修改已有角色。")
            if any(before[c]["trait"] != after[c]["trait"] for c in before) and not data.allow_locked_changes:
                raise Problem("LOCKED_FACT_CONFLICT", "修改人物核心特点需要明确确认。")
            current.update(
                brief=payload["brief"],
                chapter_plans=payload["chapter_plans"],
                characters=payload["characters"],
            )
            rev = commit_revision(s, b, current, "workspace_edit")
            return {"revision_id": rev.id}

        return command(s, user, f"workspace:{bid}", key, payload, execute)


class ManualDraft(Input):
    base_revision_id: UUID
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=50000)
    plan_id: UUID | None = None
    replace_scene_id: UUID | None = None


def validate_plan(state, plan_id, replace_scene_id=None):
    if not plan_id:
        return
    if not any(p["id"] == str(plan_id) for p in state.get("chapter_plans", [])):
        raise Problem("INVALID_PLAN", "章纲不属于当前版本。")
    if any(x.get("plan_id") == str(plan_id) and x["id"] != str(replace_scene_id) for x in state["scenes"]):
        raise Problem("PLAN_ALREADY_WRITTEN", "该章节已定稿，请使用修订或分支。")


@router.post("/branches/{bid}/manual-drafts", status_code=201)
def manual_draft(bid: UUID, data: ManualDraft, user: Owner, key: Key):
    with Session.begin() as s:
        owned_branch(s, str(bid), user)

        def execute():
            b = owned_branch(s, str(bid), user, lock=True)
            if b.head_revision_id != str(data.base_revision_id):
                raise Problem("REVISION_CONFLICT", "正文版本已更新，请刷新。", 409)
            current = snapshot(s, b.head_revision_id)
            if data.replace_scene_id and (
                not current["scenes"] or current["scenes"][-1]["id"] != str(data.replace_scene_id)
            ):
                raise Problem("BRANCH_REQUIRED", "修改较早章节会影响后文，请先从该章开分支。", 409)
            validate_plan(current, data.plan_id, data.replace_scene_id)
            job = Job(
                id=uid(),
                branch_id=b.id,
                base_revision_id=b.head_revision_id,
                request={"kind": "manual", "plan_id": str(data.plan_id) if data.plan_id else None},
                status="succeeded",
            )
            s.add(job)
            s.flush()
            content = {
                "kind": "scene",
                "title": data.title,
                "body": data.body,
                "scene_number": len(current["scenes"]) + (0 if data.replace_scene_id else 1),
                "source": "author",
                "mock": False,
                "note": "作者手写；未经自动语义审校。",
            }
            if data.plan_id:
                content["plan_id"] = str(data.plan_id)
            if data.replace_scene_id:
                content["replace_scene_id"] = str(data.replace_scene_id)
            draft = Draft(id=uid(), job_id=job.id, content=content)
            s.add(draft)
            s.add(
                Event(job_id=job.id, kind="draft.ready", payload={"draft_id": draft.id, "source": "author"})
            )
            return {"draft_id": draft.id, "job_id": job.id}

        return command(s, user, f"manual:{bid}", key, data.model_dump(mode="json"), execute)


class DraftEdit(Input):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=50000)


@router.patch("/drafts/{did}")
def edit_draft(did: UUID, data: DraftEdit, user: Owner, key: Key):
    with Session.begin() as s:
        d = s.get(Draft, str(did))
        if not d:
            raise Problem("NOT_FOUND", "草稿不存在。", 404)
        j = s.get(Job, d.job_id)
        owned_branch(s, j.branch_id, user)

        def execute():
            owned_branch(s, j.branch_id, user, lock=True)
            s.refresh(d)
            if d.status != "pending" or d.version != data.expected_version:
                raise Problem("DRAFT_CONFLICT", "草稿已更新或定稿。本地文字保留，请重新核对。", 409)
            d.content = dict(d.content, title=data.title, body=data.body, edited_by_author=True)
            d.version += 1
            s.add(Event(job_id=j.id, kind="draft.edited", payload={"version": d.version}))
            return {"id": d.id, "version": d.version, "content_hash": digest(d.content)}

        return command(s, user, f"edit:{did}", key, data.model_dump(mode="json"), execute)


@router.get("/branches/{bid}/history")
def history(bid: UUID, user: Owner):
    with Session() as s:
        b = owned_branch(s, str(bid), user)
        rows, rid = [], b.head_revision_id
        while rid and len(rows) < 100:
            revision = s.get(Revision, rid)
            if not revision:
                break
            rows.append(
                {
                    "id": rid,
                    "kind": revision.kind,
                    "created_at": revision.created_at.isoformat(),
                    "scene_count": len(revision.state["scenes"]),
                    "hash": revision.state_hash[:12],
                }
            )
            rid = revision.parent_revision_id
        return rows


@router.get("/jobs/{jid}/trace")
def trace(jid: UUID, user: Owner):
    from .models import ModelCall

    with Session() as s:
        j = s.get(Job, str(jid))
        if not j:
            raise Problem("NOT_FOUND", "任务不存在。", 404)
        owned_branch(s, j.branch_id, user)
        call = s.scalar(select(ModelCall).where(ModelCall.job_id == j.id))
        steps = []
        for step in s.scalars(select(Step).where(Step.job_id == j.id).order_by(Step.step)):
            # No raw text or private character context is exposed in the execution trace.
            result = (
                step.result if step.step != 2 else {"characters_written": len(step.result.get("body", ""))}
            )
            steps.append({"index": step.step, "result": result})
        return {
            "job_id": j.id,
            "status": j.status,
            "base_revision_id": j.base_revision_id,
            "source": j.request.get("provider", "author" if j.request["kind"] == "manual" else "mock"),
            "steps": steps,
            "model_calls": 1 if call else 0,
            "cost": None if call else 0,
            "usage": call.usage if call else None,
            "call_status": call.status if call else None,
        }
