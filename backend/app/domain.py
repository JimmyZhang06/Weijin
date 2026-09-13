import copy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from .db import uid
from .models import Branch, Receipt, Revision, World


class Problem(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.message, self.status = code, message, status


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Character(Input):
    name: str = Field(min_length=1, max_length=40)
    trait: str = Field(min_length=1, max_length=400)
    desire: str = Field(default="", max_length=400)
    secret: str = Field(default="", max_length=400)


class CreateWorld(Input):
    title: str = Field(min_length=1, max_length=120)
    opening: str = Field(min_length=1, max_length=2000)
    characters: list[Character] = Field(min_length=2, max_length=5)
    brief: "StoryBrief" = Field(default_factory=lambda: StoryBrief())


class StoryBrief(Input):
    work_type: Literal["original", "fanfic"] = "original"
    fandom: str = Field(default="", max_length=300)
    canon_anchor: str = Field(default="", max_length=1000)
    relationship: str = Field(default="", max_length=500)
    divergence: str = Field(default="", max_length=1500)
    boundaries: str = Field(default="", max_length=2000)
    style: str = Field(default="", max_length=1000)
    pov: Literal["third_limited", "first", "omniscient"] = "third_limited"


CreateWorld.model_rebuild()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def owned_branch(s, branch_id, owner, lock=False):
    query = select(Branch).join(World).where(Branch.id == branch_id, World.owner_id == owner)
    if lock:
        query = query.with_for_update(of=Branch)
    branch = s.scalar(query)
    if not branch:
        raise Problem("NOT_FOUND", "故事路线不存在。", 404)
    return branch


def snapshot(s, revision_id):
    revision = s.get(Revision, revision_id)
    if not revision or digest(revision.state) != revision.state_hash:
        raise Problem("STATE_CORRUPT", "故事状态校验失败，请保留数据并联系维护者。", 500)
    return copy.deepcopy(revision.state)


def commit_revision(s, branch, state, kind):
    revision = Revision(
        id=uid(),
        branch_id=branch.id,
        parent_revision_id=branch.head_revision_id,
        kind=kind,
        state=state,
        state_hash=digest(state),
    )
    s.add(revision)
    s.flush()
    branch.head_revision_id = revision.id
    branch.version += 1
    return revision


def command(s, owner, operation, key, payload, execute):
    if not key or len(key) > 100:
        raise Problem("IDEMPOTENCY_REQUIRED", "请提供有效的 Idempotency-Key。")
    receipt_id = digest([owner, operation, key])
    # Transaction-scoped lock also handles concurrent first requests for the same key.
    number = int(receipt_id[:15], 16)
    s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})
    receipt = s.get(Receipt, receipt_id)
    request_hash = digest(payload)
    if receipt:
        if receipt.request_hash != request_hash:
            raise Problem("IDEMPOTENCY_CONFLICT", "同一个操作标识不能用于不同内容。", 409)
        return receipt.response
    response = execute()
    s.add(Receipt(id=receipt_id, request_hash=request_hash, response=response))
    return response


def create_world(s, owner, data):
    world = World(id=uid(), owner_id=owner, title=data.title)
    branch = Branch(id=uid(), world_id=world.id, title="原来的故事", head_revision_id=uid())
    people, facts, knowledge = [], [], []
    for item in data.characters:
        cid = uid()
        people.append({"id": cid, "name": item.name, "trait": item.trait, "desire": item.desire})
        if item.secret:
            fid = uid()
            facts.append(
                {
                    "id": fid,
                    "text": item.secret,
                    "known_by": [cid],
                    "scene": 0,
                    "source": "author",
                    "locked": True,
                }
            )
            knowledge.append(
                {"character_id": cid, "text": item.secret, "scene": 0, "type": "knowledge", "source_id": fid}
            )
    state = {
        "characters": people,
        "facts": facts,
        "knowledge": knowledge,
        "opening": data.opening,
        "scenes": [],
        "artifacts": [],
        "schema_version": 1,
        "brief": data.brief.model_dump(),
        "chapter_plans": [],
    }
    s.add(world)
    s.flush()
    s.add(branch)
    s.flush()
    s.add(
        Revision(
            id=branch.head_revision_id,
            branch_id=branch.id,
            kind="initial",
            state=state,
            state_hash=digest(state),
        )
    )
    s.flush()
    return {"id": world.id, "branch_id": branch.id, "revision_id": branch.head_revision_id}


def character_context(state, character_id, scene):
    character = next((x for x in state["characters"] if x["id"] == character_id), None)
    if not character:
        raise Problem("NOT_FOUND", "人物不存在。", 404)
    return {
        "character": copy.deepcopy(character),
        "knowledge": [
            copy.deepcopy(k)
            for k in state["knowledge"]
            if k["character_id"] == character_id and k["scene"] <= scene
        ],
        "public_facts": [
            copy.deepcopy(f) for f in state["facts"] if f["known_by"] == "all" and f["scene"] <= scene
        ],
    }


def reachable(s, branch, revision_id):
    current = branch.head_revision_id
    while current:
        if current == revision_id:
            return True
        rev = s.get(Revision, current)
        current = rev.parent_revision_id if rev else None
    return False
