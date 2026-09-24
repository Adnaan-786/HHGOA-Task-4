-- PostgreSQL persistence boundary. app.persistence creates the same immutable
-- revision and approval tables through SQLAlchemy; this SQL file is the reviewable
-- deployment/migration contract for environments that run migrations separately.
CREATE TABLE IF NOT EXISTS case_revisions (
  investigation_id text PRIMARY KEY,
  case_id text NOT NULL,
  revision integer NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  request_hash text NOT NULL,
  phase text NOT NULL,
  created_at timestamptz NOT NULL,
  answer jsonb NOT NULL,
  UNIQUE(case_id, revision)
);
CREATE INDEX IF NOT EXISTS case_revisions_case_id_revision_idx ON case_revisions(case_id, revision);

CREATE TABLE IF NOT EXISTS case_approvals (
  approval_id text PRIMARY KEY,
  case_id text NOT NULL,
  action text NOT NULL,
  revision integer NOT NULL,
  role text NOT NULL CHECK (role IN ('L1', 'L2')),
  decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
  idempotency_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS case_approvals_case_id_idx ON case_approvals(case_id, revision);

-- Planned operational tables retained below for the worker/checkpoint rollout.
CREATE TABLE IF NOT EXISTS investigation_runs (
  id uuid PRIMARY KEY,
  case_id text NOT NULL,
  revision integer NOT NULL DEFAULT 1,
  state jsonb NOT NULL,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(case_id, revision)
);
CREATE TABLE IF NOT EXISTS evidence_requests (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES investigation_runs(id),
  request_type text NOT NULL,
  response text,
  simulated boolean NOT NULL DEFAULT true,
  asked_at timestamptz NOT NULL DEFAULT now(),
  answered_at timestamptz
);
CREATE TABLE IF NOT EXISTS approvals (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES investigation_runs(id),
  case_revision integer NOT NULL,
  action text NOT NULL,
  route text NOT NULL,
  decision text NOT NULL,
  actor_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
  id bigserial PRIMARY KEY,
  run_id uuid REFERENCES investigation_runs(id),
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS job_leases (
  job_key text PRIMARY KEY,
  owner text NOT NULL,
  lease_until timestamptz NOT NULL,
  attempts integer NOT NULL DEFAULT 0
);
