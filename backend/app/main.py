import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import func, select, text

from .ai_config import public_status
from .auth import owner
from .autowrite import router as autowrite_router
from .conversation import router as conversation_router
from .db import Session, now, uid
from .domain import (
    CreateWorld,
    Input,
    Problem,
    StoryBrief,
    character_context,
    command,
    commit_revision,
    create_world,
    digest,
    owned_branch,
    reachable,
    snapshot,
)
from .models import Branch, Draft, Event, Heartbeat, Job, ModelCall, Progress, Reveal, World
from .review import structure_review
from .workspace import router as workspace_router
from .workspace import validate_plan

app = FastAPI(title="未尽 · 本地故事工坊", version="0.1.0")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_only(request, call_next):
    if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        return JSONResponse({"error": {"code": "LOCAL_ONLY"}}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"error": {"code": "ORIGIN_REJECTED"}}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'"
    )
    return response


Owner = Annotated[str, Depends(owner)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=100)]


@app.exception_handler(Problem)
async def problem_handler(request, exc):
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message, "retryable": False, "request_id": uid()}},
        status_code=exc.status,
    )


@app.get("/api/v1/health")
def health():
    try:
        with Session() as s:
            s.execute(text("SELECT 1"))
            hb = s.get(Heartbeat, "local-worker")
            worker_ok = bool(hb and (now() - hb.updated_at).total_seconds() < 15)
        return {
            "api": "ok",
            "database": "ok",
            "worker": "ok" if worker_ok else "offline",
            "provider": public_status()["provider"],
            "production_ready": False,
        }
    except Exception:
        return JSONResponse({"api": "ok", "database": "unavailable", "worker": "unknown"}, 503)


@app.post("/api/v1/worlds", status_code=201)
def new_world(data: CreateWorld, user: Owner, key: Key):
    with Session.begin() as s:
        return command(s, user, "world", key, data.model_dump(), lambda: create_world(s, user, data))


@app.get("/api/v1/worlds")
def worlds(user: Owner):
    with Session() as s:
        return [
            {
                "id": w.id,
                "title": w.title,
                "branches": [
                    {"id": b.id, "title": b.title}
                    for b in s.scalars(select(Branch).where(Branch.world_id == w.id))
                ],
            }
            for w in s.scalars(
                select(World).where(World.owner_id == user).order_by(World.created_at.desc()).limit(100)
            )
        ]


@app.get("/api/v1/branches/{bid}/state")
def state(bid: UUID, user: Owner, mode: Literal["reader", "author"] = "reader"):
    with Session() as s:
        b = owned_branch(s, str(bid), user)
        data = snapshot(s, b.head_revision_id)
        unlocked = set(
            s.scalars(select(Reveal.artifact_id).where(Reveal.branch_id == b.id, Reveal.owner_id == user))
        )
        artifacts = []
        for a in data["artifacts"]:
            view = {k: a[k] for k in ["id", "kind", "title", "scene_id", "scene_number", "character_id"]}
            view["revealed"] = a["id"] in unlocked
            view["stale"] = a.get("stale", False)
            if view["revealed"] and not view["stale"]:
                view["body"] = a["body"]
            artifacts.append(view)
        result = {
            "branch_id": b.id,
            "title": b.title,
            "world_id": b.world_id,
            "revision_id": b.head_revision_id,
            "characters": data["characters"],
            "scenes": data["scenes"],
            "artifacts": artifacts,
            "provider": "mock",
            "version": b.version,
            "opening": data["opening"],
            "brief": data.get("brief", StoryBrief().model_dump()),
            "chapter_plans": data.get("chapter_plans", []),
        }
        if mode == "author":
            result["facts"] = data["facts"]
        return result


@app.get("/api/v1/branches/{bid}/characters/{cid}/context")
def context(bid: UUID, cid: UUID, user: Owner, scene: int = Query(ge=0)):
    with Session() as s:
        b = owned_branch(s, str(bid), user)
        data = snapshot(s, b.head_revision_id)
        return character_context(data, str(cid), min(scene, len(data["scenes"])))


class SceneJob(Input):
    kind: Literal["scene"]
    base_revision_id: UUID
    plan_id: UUID | None = None


class ArtifactJob(Input):
    kind: Literal["private_artifact", "perspective"]
    base_revision_id: UUID
    scene_id: UUID
    character_id: UUID
    artifact_type: Literal["diary", "unsent_letter"] = "unsent_letter"


@app.post("/api/v1/branches/{bid}/jobs", status_code=202)
def new_job(
    bid: UUID, data: Annotated[SceneJob | ArtifactJob, Field(discriminator="kind")], user: Owner, key: Key
):
    payload = data.model_dump(mode="json")
    with Session.begin() as s:

        def execute():
            b = owned_branch(s, str(bid), user, lock=True)
            if b.head_revision_id != str(data.base_revision_id):
                raise Problem("REVISION_CONFLICT", "路线已有新版本，请刷新。", 409)
            # Serialize per owner to enforce the local concurrency ceiling across branches.
            s.execute(select(World).where(World.owner_id == user).order_by(World.id).with_for_update()).all()
            count = s.scalar(
                select(func.count())
                .select_from(Job)
                .join(Branch)
                .join(World)
                .where(
                    World.owner_id == user, Job.status.in_(["queued", "running", "orchestrating", "paused"])
                )
            )
            if count >= 2:
                raise Problem("CONCURRENCY_LIMIT", "最多同时进行两个任务。", 429)
            current = snapshot(s, b.head_revision_id)
            if data.kind == "scene":
                validate_plan(current, data.plan_id)
            if data.kind != "scene":
                if not any(x["id"] == str(data.scene_id) for x in current["scenes"]):
                    raise Problem("INVALID_ANCHOR", "场景不属于当前路线。")
                character_context(current, str(data.character_id), len(current["scenes"]))
            job = Job(id=uid(), branch_id=b.id, base_revision_id=b.head_revision_id, request=payload)
            s.add(job)
            s.flush()
            s.add(Event(job_id=job.id, kind="job.queued", payload={"provider": "mock"}))
            return {"job_id": job.id, "status": "queued"}

        owned_branch(s, str(bid), user)
        return command(s, user, f"job:{bid}", key, payload, execute)


def owned_job(s, jid, user):
    job = s.get(Job, str(jid))
    if not job:
        raise Problem("NOT_FOUND", "任务不存在。", 404)
    owned_branch(s, job.branch_id, user)
    return job


@app.get("/api/v1/branches/{bid}/jobs")
def jobs(bid: UUID, user: Owner):
    with Session() as s:
        owned_branch(s, str(bid), user)
        return [
            job_response(s, j)
            for j in s.scalars(
                select(Job).where(Job.branch_id == str(bid)).order_by(Job.created_at.desc()).limit(20)
            )
        ]


def job_response(s, j):
    d = s.scalar(select(Draft).where(Draft.job_id == j.id))
    call = s.scalar(select(ModelCall).where(ModelCall.job_id == j.id))
    return {
        "job_id": j.id,
        "status": j.status,
        "step": j.next_step,
        "error": j.error,
        "draft_id": d.id if d else None,
        "draft_status": d.status if d else None,
        "provider": j.request.get("provider", "author" if j.request["kind"] == "manual" else "mock"),
        "cost": None if call else 0,
        "usage": call.usage if call else None,
        "kind": j.request["kind"],
        "plan_id": j.request.get("plan_id"),
        "draft_title": d.content.get("title") if d else None,
        "draft_kind": d.content.get("kind") if d else None,
        "draft_plan_id": d.content.get("plan_id") if d else None,
        "replace_scene_id": d.content.get("replace_scene_id") if d else None,
        "created_at": j.created_at.isoformat(),
    }


@app.get("/api/v1/jobs/{jid}")
def job_status(jid: UUID, user: Owner):
    with Session() as s:
        return job_response(s, owned_job(s, jid, user))


@app.post("/api/v1/jobs/{jid}/cancel")
def cancel(jid: UUID, user: Owner):
    with Session.begin() as s:
        owned_job(s, jid, user)
        j = s.scalar(select(Job).where(Job.id == str(jid)).with_for_update())
        if j.status in {"queued", "running"}:
            j.status = "cancelled"
            j.token += 1
            s.add(Event(job_id=j.id, kind="job.cancelled", payload={}))
        return {"status": j.status}


@app.get("/api/v1/jobs/{jid}/events")
def events(jid: UUID, user: Owner, last_event_id: Annotated[int, Header(ge=0)] = 0):
    with Session() as s:
        owned_job(s, jid, user)

    async def stream():
        cursor = last_event_id
        while True:
            with Session() as s:
                j = owned_job(s, jid, user)
                records = s.scalars(
                    select(Event).where(Event.job_id == str(jid), Event.id > cursor).order_by(Event.id)
                ).all()
                status = j.status
                for e in records:
                    cursor = e.id
                    yield f"id: {e.id}\nevent: {e.kind}\ndata: {json.dumps(e.payload)}\n\n"
            if status not in {"running", "queued"}:
                break
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/v1/drafts/{did}")
def get_draft(did: UUID, user: Owner):
    with Session() as s:
        d = s.get(Draft, str(did))
        if not d:
            raise Problem("NOT_FOUND", "草稿不存在。", 404)
        j = owned_job(s, d.job_id, user)
        return {
            "id": d.id,
            "status": d.status,
            "base_revision_id": j.base_revision_id,
            "accepted_scene_id": snapshot(s, d.accepted_revision_id)["scenes"][-1]["id"]
            if d.accepted_revision_id and d.content.get("kind") == "scene"
            else None,
            "content": d.content,
            "review": "仅执行结构与引用校验，未进行语义或人物一致性审查。",
            "version": d.version,
            "content_hash": digest(d.content),
            "job_id": j.id,
            "structure_review": structure_review(d.content, snapshot(s, j.base_revision_id)),
            "comparison": {"before": j.request.get("source_body", ""), "after": d.content["body"]}
            if j.request.get("mode") == "revise"
            else None,
        }


class Accept(Input):
    expected_revision_id: UUID
    expected_content_hash: str | None = Field(default=None, max_length=64)
    acknowledge_semantic_review: bool = False


@app.post("/api/v1/drafts/{did}/accept")
def accept(did: UUID, data: Accept, user: Owner, key: Key):
    with Session.begin() as s:
        d = s.get(Draft, str(did))
        if not d:
            raise Problem("NOT_FOUND", "草稿不存在。", 404)
        j = owned_job(s, d.job_id, user)

        def execute():
            b = owned_branch(s, j.branch_id, user, lock=True)
            s.refresh(d)
            if d.status == "accepted":
                return {"revision_id": d.accepted_revision_id}
            if d.status != "pending" or j.status != "succeeded":
                raise Problem("INVALID_DRAFT", "该草稿不可接受。", 409)
            if (
                b.head_revision_id != str(data.expected_revision_id)
                or b.head_revision_id != j.base_revision_id
            ):
                raise Problem("REVISION_CONFLICT", "已有新版本；旧稿保留，请刷新或另开路线。", 409)
            current = snapshot(s, b.head_revision_id)
            if (data.expected_content_hash and data.expected_content_hash != digest(d.content)) or (
                d.version > 1 and not data.expected_content_hash
            ):
                raise Problem("DRAFT_CONFLICT", "草稿内容已有修改，请重新阅读后定稿。", 409)
            human = d.content.get("source") in {"author", "openai", "deepseek"} or d.content.get(
                "edited_by_author"
            )
            if human and not data.acknowledge_semantic_review:
                raise Problem("AUTHOR_REVIEW_REQUIRED", "请确认已核对人物、原作设定和新增事实。")
            report = structure_review(d.content, current)
            if not report["passed"]:
                raise Problem("STRUCTURE_INVALID", "正文结构或引用存在问题，无法定稿。")
            item = dict(d.content, id=uid())
            if item["kind"] == "scene":
                item["number"] = item.pop("scene_number")
                item["before_revision_id"] = b.head_revision_id
                replace_id = item.pop("replace_scene_id", None)
                if replace_id:
                    if not current["scenes"] or current["scenes"][-1]["id"] != replace_id:
                        raise Problem("BRANCH_REQUIRED", "后文已变化，请先开分支。", 409)
                    item["before_revision_id"] = current["scenes"][-1]["before_revision_id"]
                    current["scenes"][-1] = item
                    for artifact in current["artifacts"]:
                        if artifact["scene_id"] == replace_id:
                            artifact["stale"] = True
                else:
                    current["scenes"].append(item)
                if human:
                    item["fact_sync"] = "author_reviewed_not_extracted"
            else:
                current["artifacts"].append(item)
            rev = commit_revision(s, b, current, item["kind"])
            d.status, d.accepted_revision_id = "accepted", rev.id
            return {"revision_id": rev.id}

        return command(s, user, f"accept:{did}", key, data.model_dump(mode="json"), execute)


@app.post("/api/v1/drafts/{did}/reject")
def reject(did: UUID, user: Owner):
    with Session.begin() as s:
        d = s.get(Draft, str(did))
        if not d:
            raise Problem("NOT_FOUND", "草稿不存在。", 404)
        j = owned_job(s, d.job_id, user)
        owned_branch(s, j.branch_id, user, lock=True)
        s.refresh(d)
        if d.status == "pending":
            d.status = "rejected"
        return {"status": d.status}


class Fork(Input):
    expected_revision_id: UUID
    before_scene_id: UUID
    title: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=2000)


@app.post("/api/v1/branches/{bid}/forks", status_code=201)
def fork(bid: UUID, data: Fork, user: Owner, key: Key):
    with Session.begin() as s:
        owned_branch(s, str(bid), user)

        def execute():
            parent = owned_branch(s, str(bid), user, lock=True)
            if parent.head_revision_id != str(data.expected_revision_id):
                raise Problem("REVISION_CONFLICT", "路线已有新版本。", 409)
            current = snapshot(s, parent.head_revision_id)
            scene = next((x for x in current["scenes"] if x["id"] == str(data.before_scene_id)), None)
            if not scene or not reachable(s, parent, scene["before_revision_id"]):
                raise Problem("INVALID_ANCHOR", "无法从该场景建立路线。")
            anchor = scene["before_revision_id"]
            new = Branch(
                id=uid(),
                world_id=parent.world_id,
                title=data.title,
                parent_branch_id=parent.id,
                fork_revision_id=anchor,
                head_revision_id=anchor,
                instruction=data.instruction,
            )
            s.add(new)
            s.flush()
            commit_revision(s, new, snapshot(s, anchor), "fork")
            return {"branch_id": new.id, "revision_id": new.head_revision_id, "actual_anchor": anchor}

        return command(s, user, f"fork:{bid}", key, data.model_dump(mode="json"), execute)


class Reading(Input):
    scene_count: int = Field(ge=0)


@app.put("/api/v1/branches/{bid}/reading-progress")
def progress(bid: UUID, data: Reading, user: Owner):
    with Session.begin() as s:
        b = owned_branch(s, str(bid), user, lock=True)
        count = len(snapshot(s, b.head_revision_id)["scenes"])
        if data.scene_count > count:
            raise Problem("INVALID_PROGRESS", "阅读位置超出正文。")
        p = s.scalar(select(Progress).where(Progress.branch_id == b.id, Progress.owner_id == user))
        if not p:
            p = Progress(branch_id=b.id, owner_id=user, scene_count=0)
            s.add(p)
        p.scene_count = max(p.scene_count, data.scene_count)
        return {"scene_count": p.scene_count}


@app.post("/api/v1/branches/{bid}/artifacts/{aid}/reveal")
def reveal(bid: UUID, aid: UUID, user: Owner):
    with Session.begin() as s:
        b = owned_branch(s, str(bid), user, lock=True)
        artifact = next(
            (x for x in snapshot(s, b.head_revision_id)["artifacts"] if x["id"] == str(aid)), None
        )
        if not artifact:
            raise Problem("NOT_FOUND", "档案不存在于当前路线。", 404)
        if artifact.get("stale"):
            raise Problem("STALE_ARTIFACT", "原章节已经修订，这份补充内容需要重新创作。", 409)
        p = s.scalar(select(Progress).where(Progress.branch_id == b.id, Progress.owner_id == user))
        if not p or p.scene_count < artifact["scene_number"]:
            raise Problem("READING_REQUIRED", "请先阅读对应场景。")
        exists = s.scalar(
            select(Reveal).where(
                Reveal.branch_id == b.id, Reveal.owner_id == user, Reveal.artifact_id == str(aid)
            )
        )
        if not exists:
            s.add(Reveal(branch_id=b.id, owner_id=user, artifact_id=str(aid)))
        return artifact


@app.get("/api/v1/branches/{bid}/export")
def export(bid: UUID, user: Owner):
    with Session() as s:
        b = owned_branch(s, str(bid), user)
        data = snapshot(s, b.head_revision_id)
        body = "\n\n".join(f"## {x['title']}\n\n{x['body']}" for x in data["scenes"])
        return Response(
            body,
            media_type="text/markdown",
            headers={"Content-Disposition": 'attachment; filename="story.md"'},
        )


app.include_router(workspace_router)
app.include_router(conversation_router)
app.include_router(autowrite_router)

app.mount(
    "/", StaticFiles(directory=Path(__file__).resolve().parents[2] / "frontend", html=True), name="frontend"
)
