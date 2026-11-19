-- ============================================================================
-- CA Workbench - Financial Statements module : PostgreSQL schema (DRAFT FOR REVIEW)
-- Not a migration yet. Becomes Alembic revision 0001 once the plan is approved.
-- Conventions
--   * Money: NUMERIC(18,2), rupees, never float. Balances are SIGNED: Debit = +, Credit = -.
--   * Enumerations are TEXT + CHECK (cheaper to evolve than PG enums).
--   * Every business table is audited by trigger audit_row_change() (section 9).
--   * Every transaction must run:  SET LOCAL app.user_id = '<id>'; SET LOCAL app.request_id = '<uuid>';
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- only needed if exclusion constraints are added later

-- ---------------------------------------------------------------------------
-- 0. Users, roles, permissions  (local auth; user 1 = 'system' for background jobs)
-- ---------------------------------------------------------------------------
CREATE TABLE users (
  id            BIGSERIAL PRIMARY KEY,
  email         TEXT NOT NULL,
  full_name     TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_users_email ON users (lower(email));

CREATE TABLE roles (
  code        TEXT PRIMARY KEY,                 -- PREPARER, REVIEWER, PARTNER, ADMIN
  description TEXT NOT NULL
);
CREATE TABLE user_roles (
  user_id   BIGINT NOT NULL REFERENCES users(id),
  role_code TEXT   NOT NULL REFERENCES roles(code),
  PRIMARY KEY (user_id, role_code)
);
CREATE TABLE role_permissions (               -- e.g. exception.clear, journal.approve, statement.signoff
  role_code  TEXT NOT NULL REFERENCES roles(code),
  permission TEXT NOT NULL,
  PRIMARY KEY (role_code, permission)
);

-- ---------------------------------------------------------------------------
-- 1. Schedule III templates (configuration, not code)  -- isolates Q2
-- ---------------------------------------------------------------------------
CREATE TABLE statement_templates (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,            -- 'SCH3-COMPANY', 'AM-STAT'
  name        TEXT NOT NULL,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('PRIVATE_LIMITED','PUBLIC_LIMITED','LLP','PARTNERSHIP','PROPRIETORSHIP')),
  version     INT  NOT NULL DEFAULT 1,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE template_lines (
  id                BIGSERIAL PRIMARY KEY,
  template_id       BIGINT NOT NULL REFERENCES statement_templates(id),
  code              TEXT NOT NULL,             -- stable key, e.g. 'BS.EQ.RESERVES'
  statement         TEXT NOT NULL CHECK (statement IN ('BS','PL')),
  section           TEXT NOT NULL,             -- 'Equity','Non-current liabilities','Revenue',...
  label             TEXT NOT NULL,             -- entity-type specific wording lives here
  parent_id         BIGINT REFERENCES template_lines(id),
  sort_order        INT  NOT NULL,
  calc_kind         TEXT NOT NULL CHECK (calc_kind IN ('mapped','subtotal','derived')),
  formula           JSONB,                     -- subtotal/derived only, e.g. {"sum":["PL.REV","PL.OTHINC"]}
  presentation_sign SMALLINT NOT NULL DEFAULT 1 CHECK (presentation_sign IN (1,-1)),  -- -1 flips Cr-natured lines to positive
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
  UNIQUE (id, line_id)                          -- lets mappings guarantee note belongs to the chosen line
);

CREATE TABLE template_line_crosswalk (          -- lets a template switch (AM-STAT -> company) re-map automatically
  from_line_id BIGINT NOT NULL REFERENCES template_lines(id),
  to_line_id   BIGINT NOT NULL REFERENCES template_lines(id),
  PRIMARY KEY (from_line_id, to_line_id)
);

CREATE TABLE ratio_definitions (                -- isolates Q7; formulas reference template_lines.code only
  id          BIGSERIAL PRIMARY KEY,
  template_id BIGINT NOT NULL REFERENCES statement_templates(id),
  code        TEXT NOT NULL,
  name        TEXT NOT NULL,
  formula     TEXT NOT NULL,                    -- "BS.CA / BS.CL"  (parsed by a safe Decimal evaluator, no eval())
  display     TEXT NOT NULL DEFAULT 'times' CHECK (display IN ('times','percent','days')),
  status      TEXT NOT NULL DEFAULT 'provisional' CHECK (status IN ('provisional','confirmed')),
  sort_order  INT NOT NULL DEFAULT 0,
  UNIQUE (template_id, code)
);

-- ---------------------------------------------------------------------------
-- 2. Clients, engagements, versions
-- ---------------------------------------------------------------------------
CREATE TABLE clients (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,            -- '9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED'
  cin         TEXT,
  pan         TEXT,
  entity_type TEXT NOT NULL CHECK (entity_type IN ('PRIVATE_LIMITED','PUBLIC_LIMITED','LLP','PARTNERSHIP','PROPRIETORSHIP')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE engagements (
  id                    BIGSERIAL PRIMARY KEY,
  client_id             BIGINT NOT NULL REFERENCES clients(id),
  fy_label              TEXT   NOT NULL,                 -- '2026-27'
  period_start          DATE   NOT NULL,                 -- 2026-04-01
  period_end            DATE   NOT NULL,                 -- 2027-03-31
  engagement_type       TEXT   NOT NULL DEFAULT 'STATUTORY_AUDIT',
  status                TEXT   NOT NULL DEFAULT 'planning' CHECK (status IN ('planning','fieldwork','review','completed')),
  template_id           BIGINT NOT NULL REFERENCES statement_templates(id),
  template_confirmed_by BIGINT REFERENCES users(id),     -- Q2: banner shown until set
  template_confirmed_at TIMESTAMPTZ,
  default_unit          TEXT   NOT NULL DEFAULT 'LAKH' CHECK (default_unit IN ('LAKH','RUPEE')),
  data_revision         INT    NOT NULL DEFAULT 0,       -- bumped by every statement-affecting change
  current_version_id    BIGINT,                          -- FK added below (circular)
  tb_signed_off_by      BIGINT REFERENCES users(id),     -- "TB sign-off open"
  tb_signed_off_at      TIMESTAMPTZ,
  materiality_amount    NUMERIC(18,2) CHECK (materiality_amount IS NULL OR materiality_amount >= 0),  -- T-23 / Q6
  materiality_method    TEXT CHECK (materiality_method IN ('MANUAL','CALCULATED')),
  materiality_basis     TEXT,
  materiality_set_by    BIGINT REFERENCES users(id),
  materiality_set_at    TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, fy_label, engagement_type),
  CHECK (period_end > period_start)
);

CREATE TABLE statement_versions (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  version_no    INT    NOT NULL,
  -- draft -> approved (gate cleared, snapshot frozen) -> signed_off (locked) ; any later change => superseded + new draft
  status        TEXT   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved','signed_off','superseded')),
  data_revision INT    NOT NULL,
  content_hash  TEXT,                                    -- sha256 of canonical snapshot payload
  created_by    BIGINT NOT NULL REFERENCES users(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_reason TEXT  NOT NULL,                         -- 'initial', 'mapping change after sign-off: ledger X', ...
  approved_at   TIMESTAMPTZ,
  locked_at     TIMESTAMPTZ,
  superseded_at TIMESTAMPTZ,
  UNIQUE (engagement_id, version_no),
  UNIQUE (id, engagement_id)
);
-- at most one live (non-superseded) version per engagement
CREATE UNIQUE INDEX ux_one_live_version ON statement_versions (engagement_id) WHERE status <> 'superseded';
ALTER TABLE engagements ADD CONSTRAINT fk_eng_current_version
  FOREIGN KEY (current_version_id) REFERENCES statement_versions(id) DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE statement_snapshots (               -- frozen figures at approval; written only by the system
  version_id   BIGINT PRIMARY KEY REFERENCES statement_versions(id),
  payload      JSONB  NOT NULL,                    -- BS, P&L, notes, ratios, adjusted TB hash
  content_hash TEXT   NOT NULL,
  taken_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 3. Tally import (T-01)  -- isolates Q3
-- ---------------------------------------------------------------------------
CREATE TABLE import_profiles (
  id         BIGSERIAL PRIMARY KEY,
  code       TEXT NOT NULL UNIQUE,                -- 'TALLY_TB_CSV_V1'
  name       TEXT NOT NULL,
  format     TEXT NOT NULL CHECK (format IN ('csv','xlsx','xml')),
  column_map JSONB NOT NULL,                      -- {"ledger":"Particulars","group":"Group","opening":"Opening Balance",...}
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
  id                    BIGSERIAL PRIMARY KEY,
  engagement_id         BIGINT NOT NULL REFERENCES engagements(id),
  profile_id            BIGINT REFERENCES import_profiles(id),
  source_file_name      TEXT   NOT NULL,
  file_sha256           TEXT   NOT NULL,
  uploaded_by           BIGINT NOT NULL REFERENCES users(id),
  uploaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  status                TEXT   NOT NULL CHECK (status IN ('validated','committed','failed')),
  rows_total            INT    NOT NULL DEFAULT 0,
  ledgers_inserted      INT    NOT NULL DEFAULT 0,
  ledgers_updated       INT    NOT NULL DEFAULT 0,
  ledgers_unchanged     INT    NOT NULL DEFAULT 0,
  ledgers_deactivated   INT    NOT NULL DEFAULT 0,
  total_debit           NUMERIC(18,2),
  total_credit          NUMERIC(18,2),
  closing_mismatch_count INT   NOT NULL DEFAULT 0, -- rows where opening+Dr-Cr <> closing stated in file
  message               TEXT,
  error_detail          JSONB
);
CREATE INDEX ix_upload_history_eng ON upload_history (engagement_id, uploaded_at DESC);

CREATE TABLE tb_import_rows (                    -- raw staging, kept for reproducibility/audit
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
  ledger_key      TEXT   NOT NULL,               -- Tally GUID if the profile provides one, else normalised name
  name            TEXT   NOT NULL,
  group_id        BIGINT REFERENCES tally_groups(id),
  origin          TEXT   NOT NULL DEFAULT 'tally' CHECK (origin IN ('tally','adjustment')),
  is_active       BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE = absent from latest import (never deleted: keeps mapping + history)
  first_upload_id BIGINT REFERENCES upload_history(id),
  last_upload_id  BIGINT REFERENCES upload_history(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (engagement_id, ledger_key),          -- idempotent-upsert key: re-import can never duplicate
  UNIQUE (id, engagement_id)
);
CREATE INDEX ix_ledgers_eng_active ON ledgers (engagement_id) WHERE is_active;

CREATE TABLE ledger_balances (                   -- exactly one current row per ledger (replace, not append)
  ledger_id             BIGINT PRIMARY KEY REFERENCES ledgers(id),
  engagement_id         BIGINT NOT NULL REFERENCES engagements(id),
  opening_balance       NUMERIC(18,2) NOT NULL DEFAULT 0,
  period_debit          NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (period_debit  >= 0),
  period_credit         NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (period_credit >= 0),
  closing_balance       NUMERIC(18,2) NOT NULL,
  closing_per_source    NUMERIC(18,2),           -- closing as printed by Tally, to prove reproduction
  py_closing_balance    NUMERIC(18,2),           -- comparative (prior year); NULL = not supplied
  last_upload_id        BIGINT NOT NULL REFERENCES upload_history(id),
  CONSTRAINT ck_closing_arith CHECK (closing_balance = opening_balance + period_debit - period_credit)
);
CREATE INDEX ix_lb_eng ON ledger_balances (engagement_id);

-- ---------------------------------------------------------------------------
-- 4. Mapping (T-02, T-03)
-- ---------------------------------------------------------------------------
CREATE TABLE ledger_mappings (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  ledger_id     BIGINT NOT NULL,
  line_id       BIGINT NOT NULL REFERENCES template_lines(id),
  note_id       BIGINT,
  mapped_by     BIGINT NOT NULL REFERENCES users(id),
  mapped_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_one_mapping_per_ledger UNIQUE (ledger_id),            -- exactly one line + one note per ledger
  CONSTRAINT fk_map_ledger FOREIGN KEY (ledger_id, engagement_id) REFERENCES ledgers(id, engagement_id),
  CONSTRAINT fk_map_note_line FOREIGN KEY (note_id, line_id) REFERENCES template_notes(id, line_id)  -- note must belong to the line
);
CREATE INDEX ix_map_eng_line ON ledger_mappings (engagement_id, line_id);
-- trigger trg_mapping_template_consistent: line.template_id must equal engagements.template_id (section 9)
-- "mapped" for completeness = row exists AND (note_id IS NOT NULL OR template_lines.requires_note = FALSE)

-- ---------------------------------------------------------------------------
-- 5. Adjustments (T-04, T-05)
-- ---------------------------------------------------------------------------
CREATE TABLE journal_entries (
  id                BIGSERIAL PRIMARY KEY,
  engagement_id     BIGINT NOT NULL REFERENCES engagements(id),
  entry_no          INT    NOT NULL,
  entry_date        DATE   NOT NULL,
  description       TEXT   NOT NULL CHECK (length(btrim(description)) > 0),
  status            TEXT   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
  source            TEXT   NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','reconciliation','tds','fd')),  -- WP9 hooks
  source_ref        TEXT,
  reverses_entry_id BIGINT REFERENCES journal_entries(id),
  created_by        BIGINT NOT NULL REFERENCES users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_by       BIGINT REFERENCES users(id),
  approved_at       TIMESTAMPTZ,
  UNIQUE (engagement_id, entry_no),
  UNIQUE (id, engagement_id),
  CONSTRAINT uq_one_reversal_per_entry UNIQUE (reverses_entry_id),
  CONSTRAINT ck_approved_has_approver CHECK (status = 'draft' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

CREATE TABLE journal_lines (
  id            BIGSERIAL PRIMARY KEY,
  entry_id      BIGINT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  line_no       INT    NOT NULL,
  ledger_id     BIGINT NOT NULL REFERENCES ledgers(id),
  debit         NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (debit  >= 0),
  credit        NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
  narration     TEXT,
  UNIQUE (entry_id, line_no),
  CONSTRAINT ck_one_side CHECK ((debit = 0) <> (credit = 0))             -- exactly one of Dr/Cr, non-zero
);
CREATE INDEX ix_jl_ledger ON journal_lines (ledger_id);
CREATE INDEX ix_jl_entry  ON journal_lines (entry_id);

-- ---------------------------------------------------------------------------
-- 6. Checks engine (T-11)  -- isolates Q1
-- ---------------------------------------------------------------------------
CREATE TABLE check_definitions (
  code              TEXT PRIMARY KEY,            -- CHK-01 .. CHK-06
  name              TEXT NOT NULL,
  description       TEXT NOT NULL,
  rule_type         TEXT NOT NULL,               -- key into backend checks registry
  rule_config       JSONB NOT NULL DEFAULT '{}',
  definition_status TEXT NOT NULL DEFAULT 'confirmed' CHECK (definition_status IN ('confirmed','pending_definition')),
  is_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  enabled_from_step INT,                         -- CHK-03 (notes tie) enabled after build step 4
  sort_order        INT NOT NULL
);

CREATE TABLE check_runs (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  data_revision INT    NOT NULL,
  trigger_event TEXT   NOT NULL,                 -- 'import','mapping','adjustment_approved','manual',...
  triggered_by  BIGINT NOT NULL REFERENCES users(id),
  run_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  passed_count  INT NOT NULL,
  total_count   INT NOT NULL                     -- always 6 once CHK-06 row exists (pending counts as not passed)
);
CREATE INDEX ix_check_runs_latest ON check_runs (engagement_id, id DESC);

CREATE TABLE check_results (
  id           BIGSERIAL PRIMARY KEY,
  run_id       BIGINT NOT NULL REFERENCES check_runs(id) ON DELETE CASCADE,
  check_code   TEXT   NOT NULL REFERENCES check_definitions(code),
  status       TEXT   NOT NULL CHECK (status IN ('pass','fail','not_run','pending_definition')),
  lhs_amount   NUMERIC(18,2),
  rhs_amount   NUMERIC(18,2),
  variance     NUMERIC(18,2) GENERATED ALWAYS AS (lhs_amount - rhs_amount) STORED,   -- exact rupee/paise difference
  detail       JSONB NOT NULL DEFAULT '{}',      -- e.g. failing note numbers, unmapped ledger ids
  UNIQUE (run_id, check_code),
  CONSTRAINT ck_pass_means_zero_variance CHECK (status <> 'pass' OR lhs_amount IS NULL OR lhs_amount = rhs_amount)
);

-- ---------------------------------------------------------------------------
-- 7. Issue tracker / exceptions (T-12, T-13)
-- ---------------------------------------------------------------------------
CREATE TABLE exception_causes (
  code       TEXT PRIMARY KEY,                   -- UNMAPPED_LEDGER, CHECK_FAILURE, RECON_DIFFERENCE, ...
  label      TEXT NOT NULL,
  sort_order INT NOT NULL DEFAULT 0
);

CREATE TABLE exceptions (
  id               BIGSERIAL PRIMARY KEY,
  engagement_id    BIGINT NOT NULL REFERENCES engagements(id),
  source           TEXT   NOT NULL CHECK (source IN ('mapping','check','reconciliation','manual')),
  reference_id     TEXT   NOT NULL,              -- deterministic: 'LEDGER:<ledger_key>', 'CHECK:CHK-03:NOTE:7', 'GSTR9C:<period>:<ledger_key>'
  cause_code       TEXT   NOT NULL REFERENCES exception_causes(code),
  title            TEXT   NOT NULL,
  detail           TEXT,
  amount           NUMERIC(18,2),
  fingerprint      TEXT   NOT NULL,              -- hash of the facts; a changed fingerprint on a cleared item re-opens it
  status           TEXT   NOT NULL DEFAULT 'open' CHECK (status IN ('open','cleared','approved')),
  is_condition_active BOOLEAN NOT NULL DEFAULT TRUE,   -- FALSE once the underlying condition disappeared
  owner_id         BIGINT REFERENCES users(id),
  first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  occurrences      INT NOT NULL DEFAULT 1,       -- times the detector re-observed it (instead of inserting again)
  cleared_by       BIGINT REFERENCES users(id),
  cleared_by_system BOOLEAN NOT NULL DEFAULT FALSE,
  cleared_at       TIMESTAMPTZ,
  clear_reason     TEXT,
  approved_by      BIGINT REFERENCES users(id),
  approved_at      TIMESTAMPTZ,
  approval_reason  TEXT,
  CONSTRAINT uq_exception_dedupe UNIQUE (engagement_id, source, reference_id),   -- THE anti-duplication guarantee
  CONSTRAINT ck_cleared_has_reason CHECK (status <> 'cleared'  OR (cleared_at IS NOT NULL AND length(btrim(coalesce(clear_reason,''))) > 0
                                                                 AND (cleared_by IS NOT NULL OR cleared_by_system))),
  CONSTRAINT ck_approved_has_reason CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL
                                                                 AND length(btrim(coalesce(approval_reason,''))) > 0))
);
CREATE INDEX ix_exc_open  ON exceptions (engagement_id, cause_code) WHERE status = 'open';
CREATE INDEX ix_exc_owner ON exceptions (owner_id) WHERE status = 'open';

-- ---------------------------------------------------------------------------
-- 8. Working papers, outputs, sign-off, UDIN (T-14 .. T-18)
-- ---------------------------------------------------------------------------
CREATE TABLE working_papers (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  wp_ref        TEXT   NOT NULL,                 -- 'B-12'
  title         TEXT   NOT NULL,
  file_uri      TEXT,
  prepared_by   BIGINT REFERENCES users(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (engagement_id, wp_ref),
  UNIQUE (id, engagement_id)
);

CREATE TABLE working_paper_links (
  id               BIGSERIAL PRIMARY KEY,
  engagement_id    BIGINT NOT NULL REFERENCES engagements(id),
  working_paper_id BIGINT NOT NULL,
  line_id          BIGINT REFERENCES template_lines(id),
  note_id          BIGINT REFERENCES template_notes(id),
  linked_by        BIGINT NOT NULL REFERENCES users(id),
  linked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (working_paper_id, engagement_id) REFERENCES working_papers(id, engagement_id),
  CONSTRAINT ck_line_xor_note CHECK ((line_id IS NOT NULL) <> (note_id IS NOT NULL))
);
CREATE UNIQUE INDEX ux_wpl_line ON working_paper_links (working_paper_id, line_id) WHERE line_id IS NOT NULL;
CREATE UNIQUE INDEX ux_wpl_note ON working_paper_links (working_paper_id, note_id) WHERE note_id IS NOT NULL;

CREATE TABLE output_documents (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  version_id    BIGINT NOT NULL REFERENCES statement_versions(id),
  kind          TEXT   NOT NULL CHECK (kind IN ('SCHEDULE_III','IA_REPORT','AC_PACK')),
  format        TEXT   NOT NULL CHECK (format IN ('HTML','PDF','DOCX')),      -- Q4
  file_path     TEXT   NOT NULL,
  built_from_hash TEXT NOT NULL,                 -- = statement_snapshots.content_hash at build time
  statement_date DATE  NOT NULL,                 -- T-16 shows date + version it was built from
  built_by      BIGINT NOT NULL REFERENCES users(id),
  built_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- "out of date" is computed, never stored: built_from_hash <> current live statement hash.

CREATE TABLE report_signoffs (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  version_id    BIGINT NOT NULL,
  report_type   TEXT   NOT NULL CHECK (report_type IN ('FINANCIAL_STATEMENTS','IA_REPORT')),
  signer_id     BIGINT NOT NULL REFERENCES users(id),
  signed_date   DATE   NOT NULL,
  status        TEXT   NOT NULL DEFAULT 'signed' CHECK (status IN ('signed','withdrawn')),
  withdrawn_at  TIMESTAMPTZ,
  withdrawn_reason TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (version_id, engagement_id) REFERENCES statement_versions(id, engagement_id),
  CONSTRAINT ck_withdrawn_complete CHECK (status = 'signed' OR (withdrawn_at IS NOT NULL AND withdrawn_reason IS NOT NULL))
);
CREATE UNIQUE INDEX ux_one_active_signoff ON report_signoffs (version_id, report_type) WHERE status = 'signed';

CREATE TABLE udin_records (
  id          BIGSERIAL PRIMARY KEY,
  signoff_id  BIGINT NOT NULL UNIQUE REFERENCES report_signoffs(id),
  udin        TEXT   NOT NULL CHECK (udin ~ '^[A-Z0-9]{18}$'),     -- format only; ICAI portal verification is out of scope
  recorded_by BIGINT NOT NULL REFERENCES users(id),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_udin_value ON udin_records (udin);

-- ---------------------------------------------------------------------------
-- 9. Immutable audit trail + integrity triggers
-- ---------------------------------------------------------------------------
CREATE TABLE audit_logs (
  id            BIGSERIAL PRIMARY KEY,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  user_id       BIGINT NOT NULL REFERENCES users(id),
  request_id    TEXT,
  engagement_id BIGINT,                           -- deliberately no FK: log must outlive anything
  entity_table  TEXT   NOT NULL,
  entity_id     TEXT,
  action        TEXT   NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE','EVENT')),
  event_name    TEXT,                             -- for EVENT rows: 'RECALCULATED','SIGNOFF_WITHDRAWN',...
  before_data   JSONB,
  after_data    JSONB,
  changed_fields TEXT[],
  reason        TEXT
);
CREATE INDEX ix_audit_eng_time ON audit_logs (engagement_id, occurred_at DESC);
CREATE INDEX ix_audit_entity   ON audit_logs (entity_table, entity_id);

CREATE FUNCTION audit_logs_block_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END $$;
CREATE TRIGGER trg_audit_logs_no_update BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();
CREATE TRIGGER trg_audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs
  FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_block_mutation();
-- plus: REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM app_role;  (app connects as a non-owner role)

CREATE FUNCTION audit_row_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_old jsonb; v_new jsonb; v_row jsonb; v_user bigint; v_fields text[];
BEGIN
  v_user := nullif(current_setting('app.user_id', true), '')::bigint;
  IF v_user IS NULL THEN
    RAISE EXCEPTION 'app.user_id not set: refusing un-attributed change to %', TG_TABLE_NAME;
  END IF;
  IF TG_OP = 'INSERT' THEN v_new := to_jsonb(NEW);
  ELSIF TG_OP = 'UPDATE' THEN
    v_old := to_jsonb(OLD); v_new := to_jsonb(NEW);
    IF v_old = v_new THEN RETURN NEW; END IF;                       -- no-op update, no log noise
    SELECT array_agg(key) INTO v_fields FROM jsonb_each(v_new) n WHERE v_old -> n.key IS DISTINCT FROM n.value;
  ELSE v_old := to_jsonb(OLD);
  END IF;
  v_row := coalesce(v_new, v_old);
  INSERT INTO audit_logs (user_id, request_id, engagement_id, entity_table, entity_id, action, before_data, after_data, changed_fields)
  VALUES (v_user, nullif(current_setting('app.request_id', true), ''),
          (v_row ->> 'engagement_id')::bigint, TG_TABLE_NAME,
          coalesce(v_row ->> 'id', v_row ->> 'ledger_id'), TG_OP, v_old, v_new, v_fields);
  RETURN coalesce(NEW, OLD);
END $$;

-- attach to every business table (generated in the migration from a list):
-- ledger_mappings, ledgers, ledger_balances, tally_groups, journal_entries, journal_lines, exceptions,
-- working_papers, working_paper_links, statement_versions, statement_snapshots, output_documents,
-- report_signoffs, udin_records, engagements, check_definitions, ratio_definitions, upload_history
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON ledger_mappings
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();          -- (repeat per table)

-- --- journal immutability + balance -----------------------------------------
CREATE FUNCTION journal_entries_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE','DELETE') AND OLD.status = 'approved' THEN
    RAISE EXCEPTION 'Approved journal entry % is immutable; post a reversal instead', OLD.id;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.status = 'approved' AND OLD.status = 'draft' THEN
    IF (SELECT count(*) FROM journal_lines WHERE entry_id = NEW.id) < 2 THEN
      RAISE EXCEPTION 'Entry % needs at least two lines', NEW.id;
    END IF;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_je_guard BEFORE UPDATE OR DELETE ON journal_entries
  FOR EACH ROW EXECUTE FUNCTION journal_entries_guard();

CREATE FUNCTION journal_lines_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_entry bigint := coalesce(NEW.entry_id, OLD.entry_id);
BEGIN
  IF (SELECT status FROM journal_entries WHERE id = v_entry) = 'approved' THEN
    RAISE EXCEPTION 'Lines of approved entry % are immutable', v_entry;
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_jl_guard BEFORE INSERT OR UPDATE OR DELETE ON journal_lines
  FOR EACH ROW EXECUTE FUNCTION journal_lines_guard();

CREATE FUNCTION assert_entry_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_entry bigint := coalesce(NEW.entry_id, OLD.entry_id); v_dr numeric; v_cr numeric;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM journal_entries WHERE id = v_entry) THEN RETURN NULL; END IF;  -- entry deleted
  SELECT coalesce(sum(debit),0), coalesce(sum(credit),0) INTO v_dr, v_cr FROM journal_lines WHERE entry_id = v_entry;
  IF v_dr <> v_cr THEN
    RAISE EXCEPTION 'Journal entry % unbalanced: debits % <> credits %', v_entry, v_dr, v_cr;
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_je_balanced AFTER INSERT OR UPDATE OR DELETE ON journal_lines
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION assert_entry_balanced();   -- checked at COMMIT

-- --- mapping must use the engagement's own template --------------------------
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

-- ---------------------------------------------------------------------------
-- 10. Single source of truth views
-- ---------------------------------------------------------------------------
CREATE VIEW v_adjusted_tb AS                                      -- T-06 : mapped TB + approved adjustments, ledger by ledger
SELECT l.engagement_id,
       l.id                                         AS ledger_id,
       l.name                                       AS ledger_name,
       g.name                                       AS tally_group,
       g.nature                                     AS tally_nature,
       coalesce(lb.closing_balance, 0)              AS tb_balance,
       coalesce(adj.net, 0)                         AS adjustment_net,
       coalesce(lb.closing_balance, 0) + coalesce(adj.net, 0) AS adjusted_balance,
       lb.py_closing_balance                        AS py_balance,
       m.line_id, m.note_id
FROM ledgers l
LEFT JOIN tally_groups    g   ON g.id = l.group_id
LEFT JOIN ledger_balances lb  ON lb.ledger_id = l.id
LEFT JOIN ledger_mappings m   ON m.ledger_id = l.id
LEFT JOIN (SELECT jl.ledger_id, sum(jl.debit - jl.credit) AS net
             FROM journal_lines jl JOIN journal_entries je ON je.id = jl.entry_id
            WHERE je.status = 'approved'
            GROUP BY jl.ledger_id) adj ON adj.ledger_id = l.id
WHERE l.is_active;

CREATE VIEW v_unmapped_ledgers AS                                 -- T-03: SAME predicate feeds the screen count and the exception sync
SELECT l.engagement_id, l.id AS ledger_id, l.ledger_key, l.name
FROM ledgers l
LEFT JOIN ledger_mappings m ON m.ledger_id = l.id
LEFT JOIN template_lines  tl ON tl.id = m.line_id
WHERE l.is_active
  AND (m.id IS NULL OR (m.note_id IS NULL AND tl.requires_note));

CREATE VIEW v_line_totals AS                                      -- mapped lines only; subtotals/derived lines computed in StatementService
SELECT a.engagement_id, a.line_id,
       sum(a.adjusted_balance)           AS signed_balance,
       sum(a.adjusted_balance) * tl.presentation_sign AS presented_amount,
       sum(a.py_balance)       * tl.presentation_sign AS py_presented_amount
FROM v_adjusted_tb a JOIN template_lines tl ON tl.id = a.line_id
GROUP BY a.engagement_id, a.line_id, tl.presentation_sign;

CREATE VIEW v_note_totals AS
SELECT a.engagement_id, a.note_id, a.line_id,
       sum(a.adjusted_balance) * tl.presentation_sign AS presented_amount
FROM v_adjusted_tb a JOIN template_lines tl ON tl.id = a.line_id
WHERE a.note_id IS NOT NULL
GROUP BY a.engagement_id, a.note_id, a.line_id, tl.presentation_sign;

CREATE VIEW v_latest_check_results AS
SELECT r.engagement_id, r.id AS run_id, r.run_at, r.data_revision, cr.check_code, cr.status, cr.lhs_amount, cr.rhs_amount, cr.variance, cr.detail
FROM check_runs r JOIN check_results cr ON cr.run_id = r.id
WHERE r.id = (SELECT max(id) FROM check_runs x WHERE x.engagement_id = r.engagement_id);

CREATE VIEW v_exception_summary AS                                -- Issue Tracker header AND group-by-cause use this one view => totals always agree
SELECT e.engagement_id, e.cause_code, count(*) FILTER (WHERE e.status = 'open') AS open_count,
       count(*) FILTER (WHERE e.status = 'cleared')  AS cleared_count,
       count(*) FILTER (WHERE e.status = 'approved') AS approved_count
FROM exceptions e GROUP BY e.engagement_id, e.cause_code;

CREATE VIEW v_signoff_blockers AS                                 -- T-13: one row per reason sign-off is blocked
SELECT engagement_id, 'CHECK_FAILED'::text AS reason_type, check_code AS reference, status AS detail
  FROM v_latest_check_results WHERE status <> 'pass'
UNION ALL
SELECT engagement_id, 'EXCEPTION_OPEN', e.reference_id, e.title
  FROM exceptions e WHERE e.status = 'open';

CREATE VIEW v_unlinked_lines_notes AS                             -- T-14 (statement-line and note rows without a working paper)
SELECT en.id AS engagement_id, 'line' AS target_type, tl.id AS target_id, tl.code, tl.label
  FROM engagements en JOIN template_lines tl ON tl.template_id = en.template_id AND tl.calc_kind = 'mapped'
 WHERE NOT EXISTS (SELECT 1 FROM working_paper_links w WHERE w.engagement_id = en.id AND w.line_id = tl.id)
UNION ALL
SELECT en.id, 'note', tn.id, tn.note_no, tn.title
  FROM engagements en JOIN template_notes tn ON tn.template_id = en.template_id
 WHERE NOT EXISTS (SELECT 1 FROM working_paper_links w WHERE w.engagement_id = en.id AND w.note_id = tn.id);

CREATE VIEW v_signed_without_udin AS                              -- T-18
SELECT s.* FROM report_signoffs s LEFT JOIN udin_records u ON u.signoff_id = s.id
WHERE s.status = 'signed' AND u.id IS NULL;
