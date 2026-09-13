"""Optimistic draft edits; preserves existing stories and drafts."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("ALTER TABLE drafts ADD COLUMN version integer NOT NULL DEFAULT 1")


def downgrade():
    raise RuntimeError("Draft versions protect concurrent edits; downgrade requires a reviewed migration.")
