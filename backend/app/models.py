from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, now, uid


class World(Base):
    __tablename__ = "worlds"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    world_id: Mapped[str] = mapped_column(ForeignKey("worlds.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    parent_branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"))
    fork_revision_id: Mapped[str | None] = mapped_column(String(36))
    head_revision_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=0)
    instruction: Mapped[str] = mapped_column(String(2000), default="")


class Revision(Base):
    __tablename__ = "revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"))
    kind: Mapped[str] = mapped_column(String(40))
    state: Mapped[dict] = mapped_column(JSONB)
    state_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    base_revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"))
    request: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    token: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    next_step: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Step(Base):
    __tablename__ = "job_steps"
    __table_args__ = (UniqueConstraint("job_id", "step"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    step: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSONB)


class Event(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    content: Mapped[dict] = mapped_column(JSONB)
    accepted_revision_id: Mapped[str | None] = mapped_column(ForeignKey("revisions.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Receipt(Base):
    __tablename__ = "command_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSONB)


class Reveal(Base):
    __tablename__ = "reveals"
    __table_args__ = (UniqueConstraint("owner_id", "branch_id", "artifact_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(80))
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    artifact_id: Mapped[str] = mapped_column(String(36))


class Progress(Base):
    __tablename__ = "reader_progress"
    __table_args__ = (UniqueConstraint("owner_id", "branch_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(String(80))
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    scene_count: Mapped[int] = mapped_column(Integer, default=0)


class Heartbeat(Base):
    __tablename__ = "worker_heartbeat"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), unique=True)
    model: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default="started")
    response_id: Mapped[str | None] = mapped_column(String(160))
    usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
