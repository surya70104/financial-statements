-- ============================================================================
-- 013_step13_wp9_lfar.sql : T-24 LFAR Assistant
--   * lfar_loan_facilities: the loan SCHEDULE (lender, limit, rate, security, EMI status) per facility. It deliberately has
--     NO balance column: the book balance is read live from v_adjusted_tb through ledger_code, so there is never a second
--     copy of the ledgers and a change in the main trial balance shows in LFAR at once.
--   * lfar_checklist_items: the standard LFAR checkpoints and the auditor's status and remarks. Seeded on first access.
--   * Both tables are frozen once the Partner has signed off (same trigger as the other engagement data).
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

CREATE TABLE lfar_loan_facilities (
  id               BIGSERIAL PRIMARY KEY,
  engagement_id    BIGINT NOT NULL REFERENCES engagements(id),
  lender_name      TEXT NOT NULL CHECK (length(btrim(lender_name)) > 0),
  facility_type    TEXT NOT NULL CHECK (facility_type IN ('Term Loan', 'Working Capital / CC', 'Vehicle / Equipment Loan', 'Unsecured Loan')),
  account_number   TEXT NOT NULL CHECK (length(btrim(account_number)) > 0),
  ledger_code      TEXT NOT NULL CHECK (length(btrim(ledger_code)) > 0),      -- name of an active ledger; the balance is read from v_adjusted_tb
  sanctioned_limit NUMERIC(18,2) NOT NULL CHECK (sanctioned_limit >= 0),
  interest_rate    NUMERIC(6,3) CHECK (interest_rate IS NULL OR (interest_rate >= 0 AND interest_rate <= 100)),
  security_details TEXT,
  emi_status       TEXT NOT NULL DEFAULT 'regular' CHECK (emi_status IN ('regular', 'overdue', 'restructured')),
  overdue_amount   NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (overdue_amount >= 0),
  created_by       BIGINT NOT NULL REFERENCES users(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (engagement_id, lender_name, account_number),
  CONSTRAINT ck_lfar_overdue CHECK (overdue_amount = 0 OR emi_status <> 'regular')
);

CREATE TABLE lfar_checklist_items (
  id             BIGSERIAL PRIMARY KEY,
  engagement_id  BIGINT NOT NULL REFERENCES engagements(id),
  section_code   TEXT NOT NULL CHECK (section_code ~ '^LFAR-[0-9]{2}$'),
  category       TEXT NOT NULL,
  question_text  TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'complied', 'exception_noted', 'not_applicable')),
  auditor_remarks TEXT,
  reviewed_by    BIGINT REFERENCES users(id),
  reviewed_at    TIMESTAMPTZ,
  UNIQUE (engagement_id, section_code),
  CONSTRAINT ck_lfar_reviewed CHECK ((status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
                                  OR (status <> 'pending' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  CONSTRAINT ck_lfar_exception_remarks CHECK (status <> 'exception_noted' OR length(btrim(coalesce(auditor_remarks, ''))) > 0)
);

CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON lfar_loan_facilities FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON lfar_checklist_items FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON lfar_loan_facilities FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON lfar_checklist_items FOR EACH ROW EXECUTE FUNCTION audit_row_change();
