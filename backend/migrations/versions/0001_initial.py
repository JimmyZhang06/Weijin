"""Initial local prototype schema, explicit and frozen."""

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("""
    CREATE TABLE worlds (id varchar(36) PRIMARY KEY, owner_id varchar(80) NOT NULL,
      title varchar(120) NOT NULL, created_at timestamptz NOT NULL);
    CREATE INDEX ix_world_owner ON worlds(owner_id);
    CREATE TABLE branches (id varchar(36) PRIMARY KEY, world_id varchar(36) NOT NULL REFERENCES worlds,
      title varchar(120) NOT NULL, parent_branch_id varchar(36) REFERENCES branches,
      fork_revision_id varchar(36), head_revision_id varchar(36) NOT NULL,
      version integer NOT NULL, instruction varchar(2000) NOT NULL);
    CREATE TABLE revisions (id varchar(36) PRIMARY KEY, branch_id varchar(36) NOT NULL REFERENCES branches,
      parent_revision_id varchar(36) REFERENCES revisions, kind varchar(40) NOT NULL,
      state jsonb NOT NULL, state_hash varchar(64) NOT NULL, created_at timestamptz NOT NULL);
    ALTER TABLE branches ADD CONSTRAINT branch_head_fk FOREIGN KEY (head_revision_id)
      REFERENCES revisions(id) DEFERRABLE INITIALLY DEFERRED;
    ALTER TABLE branches ADD CONSTRAINT branch_fork_fk FOREIGN KEY (fork_revision_id) REFERENCES revisions(id);
    CREATE TABLE jobs (id varchar(36) PRIMARY KEY, branch_id varchar(36) NOT NULL REFERENCES branches,
      base_revision_id varchar(36) NOT NULL REFERENCES revisions, request jsonb NOT NULL,
      status varchar(30) NOT NULL, token integer NOT NULL, lease_until timestamptz,
      next_step integer NOT NULL, error varchar(200), created_at timestamptz NOT NULL);
    CREATE INDEX ix_jobs_status ON jobs(status,created_at);
    CREATE TABLE job_steps (id varchar(36) PRIMARY KEY, job_id varchar(36) NOT NULL REFERENCES jobs,
      step integer NOT NULL, result jsonb NOT NULL, UNIQUE(job_id,step));
    CREATE TABLE job_events (id serial PRIMARY KEY, job_id varchar(36) NOT NULL REFERENCES jobs,
      kind varchar(60) NOT NULL, payload jsonb NOT NULL);
    CREATE INDEX ix_events_job ON job_events(job_id,id);
    CREATE TABLE drafts (id varchar(36) PRIMARY KEY, job_id varchar(36) NOT NULL UNIQUE REFERENCES jobs,
      status varchar(30) NOT NULL, content jsonb NOT NULL, accepted_revision_id varchar(36) REFERENCES revisions);
    CREATE TABLE command_receipts (id varchar(64) PRIMARY KEY, request_hash varchar(64) NOT NULL,
      response jsonb NOT NULL);
    CREATE TABLE reveals (id varchar(36) PRIMARY KEY, owner_id varchar(80) NOT NULL,
      branch_id varchar(36) NOT NULL REFERENCES branches, artifact_id varchar(36) NOT NULL,
      UNIQUE(owner_id,branch_id,artifact_id));
    CREATE TABLE reader_progress (id varchar(36) PRIMARY KEY, owner_id varchar(80) NOT NULL,
      branch_id varchar(36) NOT NULL REFERENCES branches, scene_count integer NOT NULL,
      UNIQUE(owner_id,branch_id));
    CREATE TABLE worker_heartbeat (id varchar(80) PRIMARY KEY, updated_at timestamptz NOT NULL);
    CREATE FUNCTION deny_revision_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN RAISE EXCEPTION 'Story revisions are immutable'; END; $$;
    CREATE TRIGGER revisions_immutable BEFORE UPDATE ON revisions
      FOR EACH ROW EXECUTE FUNCTION deny_revision_mutation();
    """)


def downgrade():
    # Deliberately irreversible: use a verified backup, never silently delete user stories.
    raise RuntimeError("Initial schema downgrade is destructive. Restore a reviewed backup instead.")
