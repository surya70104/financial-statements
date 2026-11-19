-- ============================================================================
-- 011_step11_wp9_tds_fd.sql : T-19 TDS / TCS Entry Pack and T-22 Fixed Deposit Register
--   Both registers hold DRAFT rows first. A Checker/Partner "posts" them: each row becomes one balanced, approved journal
--   entry (journal_entries / journal_lines), so it flows through v_adjusted_tb, the statements, checks and notes like any
--   other approved adjustment. Posted rows are permanent; the journal entry is the accounting record.
--   Both tables are frozen once the Partner has signed off (same trigger as the other engagement data).
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

CREATE TABLE tds_tcs_entries (
  id               BIGSERIAL PRIMARY KEY,
  engagement_id    BIGINT NOT NULL REFERENCES engagements(id),
  entry_type       TEXT NOT NULL CHECK (entry_type IN ('TDS_PAYABLE', 'TDS_RECEIVABLE', 'TCS_PAYABLE', 'TCS_RECEIVABLE')),
  section_code     TEXT NOT NULL CHECK (section_code ~ '^[0-9]{3}[A-Z]{0,4}$'),          -- e.g. 194C, 194J, 194I, 206C
  party_name       TEXT NOT NULL CHECK (length(btrim(party_name)) > 0),
  ledger_code      TEXT,                                   -- optional: name of the TDS/TCS ledger to post to (default per entry type)
  base_amount      NUMERIC(18,2) NOT NULL CHECK (base_amount > 0),
  tax_rate         NUMERIC(6,2)  NOT NULL CHECK (tax_rate > 0 AND tax_rate <= 100),
  tax_amount       NUMERIC(18,2) NOT NULL CHECK (tax_amount > 0),
  status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'posted')),
  posted_journal_id BIGINT REFERENCES journal_entries(id),
  posted_by        BIGINT REFERENCES users(id),
  posted_at        TIMESTAMPTZ,
  created_by       BIGINT NOT NULL REFERENCES users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_tds_posted CHECK ((status = 'draft' AND posted_journal_id IS NULL AND posted_at IS NULL)
                               OR (status = 'posted' AND posted_journal_id IS NOT NULL AND posted_by IS NOT NULL AND posted_at IS NOT NULL))
);
-- the same deduction cannot be registered twice (re-uploading a file is therefore harmless)
CREATE UNIQUE INDEX ux_tds_no_duplicate ON tds_tcs_entries (engagement_id, entry_type, section_code, lower(btrim(party_name)), base_amount, tax_amount);
CREATE INDEX ix_tds_eng ON tds_tcs_entries (engagement_id, status);

CREATE TABLE fixed_deposit_register (
  id                   BIGSERIAL PRIMARY KEY,
  engagement_id        BIGINT NOT NULL REFERENCES engagements(id),
  bank_name            TEXT NOT NULL CHECK (length(btrim(bank_name)) > 0),
  fd_number            TEXT NOT NULL CHECK (length(btrim(fd_number)) > 0),
  ledger_code          TEXT,                              -- optional: the bank ledger the principal sits in today (default: matched by bank name)
  principal_amount     NUMERIC(18,2) NOT NULL CHECK (principal_amount > 0),
  interest_rate        NUMERIC(6,3)  NOT NULL CHECK (interest_rate > 0 AND interest_rate <= 100),
  start_date           DATE NOT NULL,
  maturity_date        DATE NOT NULL,
  maturity_classification TEXT NOT NULL CHECK (maturity_classification IN ('current', 'non_current')),
  accrued_interest     NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (accrued_interest >= 0),
  schedule_line_code   TEXT NOT NULL CHECK (schedule_line_code IN ('BS.CA.CASH', 'BS.CA.OTH', 'BS.NCA.OTH')),
  status               TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'posted')),
  posted_journal_id    BIGINT REFERENCES journal_entries(id),
  posted_by            BIGINT REFERENCES users(id),
  posted_at            TIMESTAMPTZ,
  created_by           BIGINT NOT NULL REFERENCES users(id),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (engagement_id, bank_name, fd_number),
  CONSTRAINT ck_fd_dates CHECK (maturity_date > start_date),
  CONSTRAINT ck_fd_line_class CHECK ((maturity_classification = 'current' AND schedule_line_code IN ('BS.CA.CASH', 'BS.CA.OTH'))
                                  OR (maturity_classification = 'non_current' AND schedule_line_code = 'BS.NCA.OTH')),
  CONSTRAINT ck_fd_posted CHECK ((status = 'draft' AND posted_journal_id IS NULL AND posted_at IS NULL)
                              OR (status = 'posted' AND posted_journal_id IS NOT NULL AND posted_by IS NOT NULL AND posted_at IS NOT NULL))
);
CREATE INDEX ix_fd_eng ON fixed_deposit_register (engagement_id, status);

-- A posted row is permanent; a draft may be posted (draft -> posted, nothing else changes) or deleted.
CREATE FUNCTION register_row_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
  IF OLD.status = 'posted' THEN RAISE EXCEPTION 'A posted % row is permanent; reverse its journal entry instead', TG_TABLE_NAME; END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  IF NEW.status = 'draft' THEN RAISE EXCEPTION 'A draft % row cannot be edited; delete it and add it again', TG_TABLE_NAME; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_register_guard BEFORE INSERT OR UPDATE OR DELETE ON tds_tcs_entries FOR EACH ROW EXECUTE FUNCTION register_row_guard();
CREATE TRIGGER trg_register_guard BEFORE INSERT OR UPDATE OR DELETE ON fixed_deposit_register FOR EACH ROW EXECUTE FUNCTION register_row_guard();

CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON tds_tcs_entries        FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON fixed_deposit_register FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON tds_tcs_entries        FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON fixed_deposit_register FOR EACH ROW EXECUTE FUNCTION audit_row_change();
