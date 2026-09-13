"""Bounded, durable book drafting. Each paid step uses the existing fenced worker."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from .ai_config import public_status, settings
from .conversation import Key, Owner
from .db import Session, uid
from .domain import Input, Problem, command, owned_branch, snapshot
from .models import Branch, Draft, Event, Job, ModelCall, Step, World

router = APIRouter(prefix="/api/v1")


class Start(Input):
    base_revision_id: UUID
    text: str = Field(min_length=1, max_length=4000)
    chapter_count: int = Field(default=3, ge=1, le=8)
    target_words: int = Field(default=1200, ge=300, le=2500)


def children(s, j):
    return [s.get(Job, x) for x in j.request.get("children", [])]


def result(s, child):
    row = s.scalar(select(Step).where(Step.job_id == child.id, Step.step == 2))
    return row.result if row and child.status == "succeeded" else None


def owned_run(s, rid, user, lock=False):
    j = s.scalar(select(Job).where(Job.id == str(rid)).with_for_update()) if lock else s.get(Job, str(rid))
    if not j or j.request.get("kind") != "autowrite":
        raise Problem("NOT_FOUND", "写作任务不存在。", 404)
    owned_branch(s, j.branch_id, user)
    return j


@router.post("/branches/{bid}/autowrite", status_code=202)
def start(bid: UUID, data: Start, user: Owner, key: Key):
    with Session.begin() as s:
        owned_branch(s, str(bid), user)

        def execute():
            # Serialize starts across this owner's worlds before checking active runs.
            b = owned_branch(s, str(bid), user, lock=True)
            s.execute(select(World).where(World.owner_id == user).order_by(World.id).with_for_update()).all()
            if b.head_revision_id != str(data.base_revision_id):
                raise Problem("REVISION_CONFLICT", "作品已更新，请刷新后开始。", 409)
            if not public_status()["configured"]:
                raise Problem("AI_NOT_CONFIGURED", public_status()["message"], 503)
            active = s.scalars(
                select(Job)
                .join(Branch)
                .join(World)
                .where(
                    World.owner_id == user, Job.status.in_(["queued", "running", "orchestrating", "paused"])
                )
            ).all()
            if any(j.branch_id == b.id for j in active) or len(active) >= 2:
                raise Problem("CONCURRENCY_LIMIT", "请先完成或停止现有任务。", 429)
            cfg = settings()
            payload = dict(
                data.model_dump(mode="json"),
                kind="autowrite",
                children=[],
                imports=[],
                provider=cfg["provider"],
                model=cfg["model"],
                max_output_tokens=cfg["max_output_tokens"],
                timeout=cfg["timeout"],
            )
            j = Job(
                id=uid(),
                branch_id=b.id,
                base_revision_id=b.head_revision_id,
                request=payload,
                status="orchestrating",
            )
            s.add(j)
            return {"id": j.id}

        return command(s, user, f"autowrite:{bid}", key, data.model_dump(mode="json"), execute)


def accepted_descendant(s, draft_id, branch_id):
    """Resolve only explicit revise ancestry, never titles or chapter numbers."""
    root = s.get(Draft, draft_id)
    if root and root.status == "accepted":
        return root
    rows = s.execute(
        select(Draft, Job)
        .join(Job, Draft.job_id == Job.id)
        .where(Job.branch_id == branch_id, Job.request["mode"].astext == "revise")
    ).all()
    reachable_ids = {draft_id}
    for _ in range(len(rows) + 1):
        additions = {d.id for d, j in rows if j.request.get("draft_id") in reachable_ids}
        if additions.issubset(reachable_ids):
            break
        reachable_ids.update(additions)
    accepted = [d for d, _ in rows if d.id in reachable_ids and d.status == "accepted"]
    return accepted[0] if len(accepted) == 1 else None


def describe(s, j):
    cs = children(s, j)
    outputs = [result(s, c) for c in cs]
    calls = s.scalars(select(ModelCall).where(ModelCall.job_id.in_([c.id for c in cs]))).all()
    return {
        "id": j.id,
        "status": j.status,
        "error": j.error,
        "text": j.request["text"],
        "chapter_count": j.request["chapter_count"],
        "target_words": j.request["target_words"],
        "model": j.request["model"],
        "calls": len(calls),
        "call_limit": j.request["chapter_count"] + 1,
        "output_token_limit": (j.request["chapter_count"] + 1) * j.request["max_output_tokens"],
        "usage": {
            k: sum((c.usage or {}).get(k, 0) for c in calls) for k in ("input_tokens", "output_tokens")
        },
        "outline": (outputs[0] or {}).get("chapters", []) if outputs else [],
        "chapters": [o for o in outputs[1:] if o],
        "current_stage": "outline" if len(cs) <= 1 else "chapter",
        "current_status": cs[-1].status if cs else "queued",
        "imports": j.request.get("imports", []),
        "import_statuses": [
            "accepted" if accepted_descendant(s, did, j.branch_id) else s.get(Draft, did).status
            for did in j.request.get("imports", [])
        ],
        "base_revision_id": j.base_revision_id,
    }


@router.get("/branches/{bid}/autowrite")
def runs(bid: UUID, user: Owner):
    with Session() as s:
        owned_branch(s, str(bid), user)
        return [
            describe(s, j)
            for j in s.scalars(
                select(Job)
                .where(Job.branch_id == str(bid), Job.request["kind"].astext == "autowrite")
                .order_by(Job.created_at.desc())
                .limit(20)
            )
        ]


@router.post("/autowrite/{rid}/{action}")
def control(rid: UUID, action: Literal["pause", "resume", "stop"], user: Owner):
    with Session.begin() as s:
        j = owned_run(s, rid, user, lock=True)
        if action == "pause" and j.status == "orchestrating":
            j.status = "paused"  # Let the issued chapter finish, but dispatch no next call.
        elif action == "resume" and j.status == "paused":
            if not public_status()["configured"]:
                raise Problem("AI_NOT_CONFIGURED", public_status()["message"], 503)
            j.status = "orchestrating"
        elif action == "stop" and j.status in {"orchestrating", "paused"}:
            j.status = "cancelled"
            for child in children(s, j):
                child = s.scalar(select(Job).where(Job.id == child.id).with_for_update())
                if child.status in {"queued", "running"}:
                    child.status, child.token = "cancelled", child.token + 1
                    s.add(Event(job_id=child.id, kind="job.cancelled", payload={}))
        return {"status": j.status}


@router.post("/autowrite/{rid}/chapters/{index}/draft")
def adopt(rid: UUID, index: int, user: Owner, key: Key):
    with Session.begin() as s:
        j = owned_run(s, rid, user, lock=True)

        def execute():
            cs = children(s, j)
            imports = list(j.request.get("imports", []))
            if 0 <= index < len(imports):
                resolved = accepted_descendant(s, imports[index], j.branch_id)
                return {"draft_id": resolved.id if resolved else imports[index]}
            if index != len(imports) or index + 1 >= len(cs):
                raise Problem("CHAPTER_ORDER", "请按顺序审阅并定稿前一章。", 409)
            chapter = result(s, cs[index + 1])
            if not chapter:
                raise Problem("CHAPTER_NOT_READY", "该章尚未完成。", 409)
            b = owned_branch(s, j.branch_id, user, lock=True)
            expected = j.base_revision_id
            if imports:
                previous = accepted_descendant(s, imports[-1], j.branch_id)
                if not previous:
                    raise Problem(
                        "PREVIOUS_CHAPTER_PENDING", "前一章尚未定稿，请定稿原稿或基于它修改的稿件。", 409
                    )
                expected = previous.accepted_revision_id
            if not expected or b.head_revision_id != expected:
                raise Problem(
                    "REVISION_CONFLICT",
                    "前章已处理，但作品版本随后发生变化。请核对设定或其他章节的修改；原始初稿仍可在连续写作中阅读。",
                    409,
                )
            child = Job(
                id=uid(),
                branch_id=b.id,
                base_revision_id=b.head_revision_id,
                request={"kind": "manual", "provider": j.request.get("provider", "openai"), "batch_id": j.id},
                status="succeeded",
            )
            s.add(child)
            s.flush()
            d = Draft(
                id=uid(),
                job_id=child.id,
                content={
                    "kind": "scene",
                    "title": chapter["title"],
                    "body": chapter["body"],
                    "source": j.request.get("provider", "openai"),
                    "mock": False,
                    "scene_number": len(snapshot(s, b.head_revision_id)["scenes"]) + 1,
                    "note": "全 AI 写作初稿；请核对情节、人物与新增事实后定稿。",
                },
            )
            s.add(d)
            j.request = dict(j.request, imports=[*imports, d.id])
            return {"draft_id": d.id}

        return command(s, user, f"adopt:{rid}:{index}", key, {}, execute)


def advance():
    """Atomic coordinator tick; no model call or long transaction here."""
    with Session.begin() as s:
        parents = s.scalars(
            select(Job)
            .where(Job.status == "orchestrating")
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
        ).all()
        for j in parents:
            cs = children(s, j)
            if cs and cs[-1].status in {"queued", "running"}:
                continue
            if cs and cs[-1].status != "succeeded":
                j.status, j.error = "failed", cs[-1].error or "CHILD_STOPPED"
                continue
            if len(cs) == j.request["chapter_count"] + 1:
                j.status = "succeeded"
                continue
            state = snapshot(s, j.base_revision_id)
            outline = result(s, cs[0])["chapters"] if cs else []
            previous = [result(s, c) for c in cs[1:]]
            index = len(previous)
            text = j.request["text"]
            if not cs:
                text += (
                    f"\n规划接下来的 {j.request['chapter_count']} 章，每章约 {j.request['target_words']} 字。"
                )
                text += "\n已有章纲：" + str(state.get("chapter_plans", []))
            else:
                text += f"\n现在写第 {index + 1} 章《{outline[index]['title']}》，约 {j.request['target_words']} 字。"
                text += "\n本章方向：" + outline[index]["summary"]
                text += "\n承接 previous_draft_chapters 的实际情节；这些是本轮初稿，不是已定稿事实。"
            request = dict(
                kind="conversation",
                batch_id=j.id,
                provider=j.request.get("provider", "openai"),
                mode="write" if cs else "outline",
                text=text,
                model=j.request["model"],
                timeout=j.request["timeout"],
                max_output_tokens=j.request["max_output_tokens"],
                prompt_version="autowrite-1",
                chapter_count=j.request["chapter_count"],
                batch_outline=outline,
                previous_draft_chapters=previous[-3:],
                history=[],
                scope="autowrite",
            )
            from .openai_provider import build_input

            try:
                build_input(state, request, s.get(Branch, j.branch_id).instruction)
            except Problem as exc:
                j.status, j.error = "failed", exc.code
                continue
            child = Job(id=uid(), branch_id=j.branch_id, base_revision_id=j.base_revision_id, request=request)
            s.add(child)
            j.request = dict(j.request, children=[*[c.id for c in cs], child.id])
