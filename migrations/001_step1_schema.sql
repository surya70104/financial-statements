-- ============================================================================
-- 001_step1_schema.sql : CA Workbench Financial Statements, Build Step 1 (WP1: T-01, T-02, T-03)
-- Money: NUMERIC(18,2) rupees, SIGNED (Debit = +, Credit = -).
-- Every transaction that writes must first run:
--     SELECT set_config('app.user_id', '<id>', true);   -- the app does this for you
-- ============================================================================

-- 0. Identity ---------------------------------------------------------------
CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  email         TEXT NOT NULL,
  full_name     TEXT NOT NULL,
  password_hash TEXT NOT NULL,               -- Step 1 uses a dev role switcher; real auth arrives later
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_users_email ON users (lower(email));

CREATE TABLE roles (
  code        TEXT PRIMARY KEY,              -- MAKER, CHECKER, PARTNER, SYSTEM
  description TEXT NOT NULL
);

CREATE TABLE user_roles (
  user_id   BIGINT NOT NULL REFERENCES users(id),
  role_code TEXT   NOT NULL REFERENCES roles(code),
  PRIMARY KEY (user_id, role_code)
);

-- 1. Schedule III templates -------------------------------------------------
CREATE TABLE statement_templates (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('PRIVATE_LIMITED','PUBLIC_LIMITED','LLP','PARTNERSHIP','PROPRIETORSHIP')),
  version     INT  NOT NULL DEFAULT 1,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE template_lines (
  id                BIGSERIAL PRIMARY KEY,
  template_id       BIGINT NOT NULL REFERENCES statement_templates(id),
  code              TEXT NOT NULL,
  statement         TEXT NOT NULL CHECK (statement IN ('BS','PL')),
  section           TEXT NOT NULL,
  label             TEXT NOT NULL,
  parent_id         BIGINT REFERENCES template_lines(id),
  sort_order        INT  NOT NULL,
  calc_kind         TEXT NOT NULL CHECK (calc_kind IN ('mapped','subtotal','derived')),
  formula           JSONB,
  presentation_sign SMALLINT NOT NULL DEFAULT 1 CHECK (presentation_sign IN (1,-1)),
  requires_note     BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE (template_id, code)
);

CREATE TABLE template_notes (
  id          BIGSERIAL PRIMARY KEY,
  template_id BIGINT NOT NULL REFERENCES statement_templates(id),
  line_id     BIGINT NOT NULL REFERENCES template_lines(id),
  note_no     TEXT   NOT NULL,
  title       TEXT   NOT NULL,
  sort_order  INT    NOT NULL,
  UNIQUE (template_id, note_no),
  UNIQUE (id, line_id)                        -- a mapping's note must belong to the mapping's line
);

-- 2. Clients, engagements, versions ----------------------------------------
CREATE TABLE clients (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  cin         TEXT,
  pan         TEXT,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('PRIVATE_LIMITED','PUBLIC_LIMITED','LLP','PARTNERSHIP','PROPRIETORSHIP')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE engagements (
  id                    BIGSERIAL PRIMARY KEY,
  client_id             BIGINT NOT NULL REFERENCES clients(id),
  fy_label              TEXT   NOT NULL,
  period_start          DATE   NOT NULL,
  period_end            DATE   NOT NULL,
  engagement_type       TEXT   NOT NULL DEFAULT 'STATUTORY_AUDIT',
  status                TEXT   NOT NULL DEFAULT 'planning' CHECK (status IN ('planning','fieldwork','review','completed')),
  template_id           BIGINT NOT NULL REFERENCES statement_templates(id),
  template_confirmed_by BIGINT REFERENCES users(id),
  template_confirmed_at TIMESTAMPTZ,
  default_unit          TEXT   NOT NULL DEFAULT 'LAKH' CHECK (default_unit IN ('LAKH','RUPEE')),
  data_revision         INT    NOT NULL DEFAULT 0,
  current_version_id    BIGINT,
  tb_signed_off_by      BIGINT REFERENCES users(id),
  tb_signed_off_at      TIMESTAMPTZ,
  materiality_amount    NUMERIC(18,2) CHECK (materiality_amount IS NULL OR materiality_amount >= 0),
  materiality_method    TEXT CHECK (materiality_method IN ('MANUAL','CALCULATED')),
  materiality_basis     TEXT,
  materiality_set_by    BIGINT REFERENCES users(id),
  materiality_set_at    TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, fy_label, engagement_type),
  CHECK (period_end > period_start)
);

CREATE TABLE statement_versions (
  id             BIGSERIAL PRIMARY KEY,
  engagement_id  BIGINT NOT NULL REFERENCES engagements(id),
  version_no     INT    NOT NULL,
  status         TEXT   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','signed_off','superseded')),
  data_revision  INT    NOT NULL,
  content_hash   TEXT,
  created_by     BIGINT NOT NULL REFERENCES users(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_reason TEXT   NOT NULL,
  approved_at    TIMESTAMPTZ,
  locked_at      TIMESTAMPTZ,
  superseded_at  TIMESTAMPTZ,
  UNIQUE (engagement_id, version_no),
  UNIQUE (id, engagement_id)
);
CREATE UNIQUE INDEX ux_one_live_version ON statement_versions (engagement_id) WHERE status <> 'superseded';
ALTER TABLE engagements ADD CONSTRAINT fk_eng_current_version
  FOREIGN KEY (current_version_id) REFERENCES statement_versions(id) DEFERRABLE INITIALLY DEFERRED;

-- 3. Tally import (T-01) ----------------------------------------------------
CREATE TABLE import_profiles (
  id         BIGSERIAL PRIMARY KEY,
  code       TEXT NOT NULL UNIQUE,
  name       TEXT NOT NULL,
  format     TEXT NOT NULL CHECK (format IN ('csv','xlsx','xml')),
  column_map JSONB NOT NULL,                   -- header aliases per logical field (Q3 lives here)
  is_default BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE tally_groups (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  name          TEXT   NOT NULL,
  parent_id     BIGINT REFERENCES tally_groups(id),
  nature        TEXT   NOT NULL CHECK (nature IN ('ASSET','LIABILITY','INCOME','EXPENSE')),
  UNIQUE (engagement_id, name)
);

CREATE TABLE upload_history (
  id                     BIGSERIAL PRIMARY KEY,
  engagement_id          BIGINT NOT NULL REFERENCES engagements(id),
  profile_id             BIGINT REFERENCES import_profiles(id),
  source_file_name       TEXT   NOT NULL,
  file_sha256            TEXT   NOT NULL,
  uploaded_by            BIGINT NOT NULL REFERENCES users(id),
  uploaded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  status                 TEXT   NOT NULL CHECK (status IN ('validated','committed','failed')),
  rows_total             INT    NOT NULL DEFAULT 0,
  ledgers_inserted       INT    NOT NULL DEFAULT 0,
  ledgers_updated        INT    NOT NULL DEFAULT 0,
  ledgers_unchanged      INT    NOT NULL DEFAULT 0,
  ledgers_deactivated    INT    NOT NULL DEFAULT 0,
  total_debit            NUMERIC(18,2),
  total_credit           NUMERIC(18,2),
  closing_mismatch_count INT    NOT NULL DEFAULT 0,
  message                TEXT,
  error_detail           JSONB
);
CREATE INDEX ix_upload_history_eng ON upload_history (engagement_id, uploaded_at DESC);

CREATE TABLE tb_import_rows (
  id         BIGSERIAL PRIMARY KEY,
  upload_id  BIGINT NOT NULL REFERENCES upload_history(id) ON DELETE CASCADE,
  row_no     INT    NOT NULL,
  ledger_key TEXT,
  raw        JSONB  NOT NULL,
  parse_note TEXT,
  UNIQUE (upload_id, row_no)
);

CREATE TABLE ledgers (
  id              BIGSERIAL PRIMARY KEY,
  engagement_id   BIGINT NOT NULL REFERENCES engagements(id),
  ledger_key      TEXT   NOT NULL,             -- normalised name (or Tally GUID when a profile provides one)
  name            TEXT   NOT NULL,
  group_id        BIGINT REFERENCES tally_groups(id),
  origin          TEXT   NOT NULL DEFAULT 'tally' CHECK (origin IN ('tally','adjustment')),
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,   -- FALSE = absent from the latest import; never deleted
  first_upload_id BIGINT REFERENCES upload_history(id),
  last_upload_id  BIGINT REFERENCES upload_history(id),   -- last upload that CHANGED this ledger
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (engagement_id, ledger_key),          -- idempotent-upsert key: re-import cannot duplicate
  UNIQUE (id, engagement_id)
);
CREATE INDEX ix_ledgers_eng_active ON ledgers (engagement_id) WHERE is_active;

CREATE TABLE ledger_balances (
  ledger_id          BIGINT PRIMARY KEY REFERENCES ledgers(id),
  engagement_id      BIGINT NOT NULL REFERENCES engagements(id),
  opening_balance    NUMERIC(18,2) NOT NULL DEFAULT 0,
  period_debit       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (period_debit  >= 0),
  period_credit      NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (period_credit >= 0),
  closing_balance    NUMERIC(18,2) NOT NULL,
  closing_per_source NUMERIC(18,2),
  py_closing_balance NUMERIC(18,2),
  last_upload_id     BIGINT NOT NULL REFERENCES upload_history(id),
  CONSTRAINT ck_closing_arith CHECK (closing_balance = opening_balance + period_debit - period_credit)
);
CREATE INDEX ix_lb_eng ON ledger_balances (engagement_id);

-- 4. Mapping (T-02) ---------------------------------------------------------
CREATE TABLE ledger_mappings (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  ledger_id     BIGINT NOT NULL,
  line_id       BIGINT NOT NULL REFERENCES template_lines(id),
  note_id       BIGINT,
  mapped_by     BIGINT NOT NULL REFERENCES users(id),
  mapped_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_one_mapping_per_ledger UNIQUE (ledger_id),
  CONSTRAINT fk_map_ledger FOREIGN KEY (ledger_id, engagement_id) REFERENCES ledgers(id, engagement_id),
  CONSTRAINT fk_map_note_line FOREIGN KEY (note_id, line_id) REFERENCES template_notes(id, line_id)
);
CREATE INDEX ix_map_eng_line ON ledger_mappings (engagement_id, line_id);

-- 5. Issue tracker (T-03 writes to it; the full register UI arrives in Step 6) --
CREATE TABLE exception_causes (
  code       TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  sort_order INT NOT NULL DEFAULT 0
);

CREATE TABLE exceptions (
  id                  BIGSERIAL PRIMARY KEY,
  engagement_id       BIGINT NOT NULL REFERENCES engagements(id),
  source              TEXT   NOT NULL CHECK (source IN ('mapping','check','reconciliation','manual')),
  reference_id        TEXT   NOT NULL,
  cause_code          TEXT   NOT NULL REFERENCES exception_causes(code),
  title               TEXT   NOT NULL,
  detail              TEXT,
  amount              NUMERIC(18,2),
  fingerprint         TEXT   NOT NULL,
  status              TEXT   NOT NULL DEFAULT 'open' CHECK (status IN ('open','cleared','approved')),
  is_condition_active BOOLEAN NOT NULL DEFAULT TRUE,
  owner_id            BIGINT REFERENCES users(id),
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  occurrences         INT NOT NULL DEFAULT 1,
  cleared_by          BIGINT REFERENCES users(id),
  cleared_by_system   BOOLEAN NOT NULL DEFAULT FALSE,
  cleared_at          TIMESTAMPTZ,
  clear_reason        TEXT,
  approved_by         BIGINT REFERENCES users(id),
  approved_at         TIMESTAMPTZ,
  approval_reason     TEXT,
  CONSTRAINT uq_exception_dedupe UNIQUE (engagement_id, source, reference_id),
  CONSTRAINT ck_cleared_has_reason CHECK (status <> 'cleared' OR (cleared_at IS NOT NULL
        AND length(btrim(coalesce(clear_reason,''))) > 0 AND (cleared_by IS NOT NULL OR cleared_by_system))),
  CONSTRAINT ck_approved_has_reason CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL
        AND length(btrim(coalesce(approval_reason,''))) > 0))
);
CREATE INDEX ix_exc_open ON exceptions (engagement_id, cause_code) WHERE status = 'open';

-- 6. Immutable audit trail --------------------------------------------------
CREATE TABLE audit_logs (
  id             BIGSERIAL PRIMARY KEY,
  occurred_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  user_id        BIGINT NOT NULL REFERENCES users(id),
  request_id     TEXT,
  engagement_id  BIGINT,                         -- no FK on purpose: the log must outlive anything
  entity_table   TEXT   NOT NULL,
  entity_id      TEXT,
  action         TEXT   NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE','EVENT')),
  event_name     TEXT,
  before_data    JSONB,
  after_data     JSONB,
  changed_fields TEXT[],
  reason         TEXT
);
CREATE INDEX ix_audit_eng_time ON audit_logs (engagement_id, occurred_at DESC);
CREATE INDEX ix_audit_entity   ON audit_logs (entity_table, entity_id);

CREATE FUNCTION audit_logs_block_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END $$;
CREATE TRIGGER trg_audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();
CREATE TRIGGER trg_audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs
  FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_block_mutation();

CREATE FUNCTION audit_row_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_old jsonb; v_new jsonb; v_row jsonb; v_user bigint; v_fields text[];
BEGIN
  v_user := nullif(current_setting('app.user_id', true), '')::bigint;
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'app.user_id not set: refusing un-attributed change to %', TG_TABLE_NAME;
  END IF;
  IF TG_OP = 'INSERT' THEN
    v_new := to_jsonb(NEW);
  ELSIF TG_OP = 'UPDATE' THEN
    v_old := to_jsonb(OLD); v_new := to_jsonb(NEW);
    IF v_old = v_new THEN RETURN NEW; END IF;
    SELECT array_agg(n.key) INTO v_fields FROM jsonb_each(v_new) n WHERE (v_old -> n.key) IS DISTINCT FROM n.value;
  ELSE
    v_old := to_jsonb(OLD);
  END IF;
  v_row := coalesce(v_new, v_old);
  INSERT INTO audit_logs (user_id, request_id, engagement_id, entity_table, entity_id, action, before_data, after_data, changed_fields)
  VALUES (v_user, nullif(current_setting('app.request_id', true), ''),
          coalesce((v_row ->> 'engagement_id')::bigint,
                   CASE WHEN TG_TABLE_NAME = 'engagements' THEN (v_row ->> 'id')::bigint END), TG_TABLE_NAME,
          coalesce(v_row ->> 'id', v_row ->> 'ledger_id'), TG_OP, v_old, v_new, v_fields);
  RETURN coalesce(NEW, OLD);
END $$;

CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON engagements        FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON statement_versions FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON tally_groups       FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON upload_history     FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON ledgers            FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON ledger_balances    FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON ledger_mappings    FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON exceptions         FOR EACH ROW EXECUTE FUNCTION audit_row_change();

-- 7. Integrity: a mapping must use the engagement's own template ---------------
CREATE FUNCTION mapping_template_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT template_id FROM template_lines WHERE id = NEW.line_id)
     IS DISTINCT FROM (SELECT template_id FROM engagements WHERE id = NEW.engagement_id) THEN
    RAISE EXCEPTION 'Line % does not belong to the engagement template', NEW.line_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_mapping_template BEFORE INSERT OR UPDATE ON ledger_mappings
  FOR EACH ROW EXECUTE FUNCTION mapping_template_consistent();

-- 8. Views --------------------------------------------------------------------
-- v_adjusted_tb: mapped TB + approved adjustments, ledger by ledger (T-06).
-- Step 1 has no journal tables yet, so adjustment_net is 0. Migration for Step 5 replaces this view
-- with CREATE OR REPLACE VIEW adding the approved-journal-lines join; column list stays identical.
CREATE VIEW v_adjusted_tb AS
SELECT l.engagement_id,
       l.id                                   AS ledger_id,
       l.name                                 AS ledger_name,
       g.name                                 AS tally_group,
       g.nature                               AS tally_nature,
       coalesce(lb.closing_balance, 0)        AS tb_balance,
       0::numeric(18,2)                       AS adjustment_net,
       coalesce(lb.closing_balance, 0)        AS adjusted_balance,
       lb.py_closing_balance                  AS py_balance,
       m.line_id,
       m.note_id
FROM ledgers l
LEFT JOIN tally_groups    g  ON g.id = l.group_id
LEFT JOIN ledger_balances lb ON lb.ledger_id = l.id
LEFT JOIN ledger_mappings m  ON m.ledger_id = l.id
WHERE l.is_active;

-- v_unmapped_ledgers: the SINGLE predicate behind the Mapping screen count and the exception sync (T-03).
CREATE VIEW v_unmapped_ledgers AS
SELECT l.engagement_id, l.id AS ledger_id, l.ledger_key, l.name
FROM ledgers l
LEFT JOIN ledger_mappings m  ON m.ledger_id = l.id
LEFT JOIN template_lines  tl ON tl.id = m.line_id
WHERE l.is_active
  AND (m.id IS NULL OR (m.note_id IS NULL AND tl.requires_note));
