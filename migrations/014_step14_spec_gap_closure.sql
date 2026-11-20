-- ============================================================================
-- 014_step14_spec_gap_closure.sql : closes the acceptance-criteria gaps found when auditing the build against the brief
--   * T-12  exceptions already have owner_id (the assigned user) and approved_by / approved_at / approval_reason (the approval),
--           so they are reused, not duplicated. New: is_above_materiality, and source 'udin'.
--   * T-16  audit_pack_builds: the data revision the Audit Pack was last built or exported at (stale-pack detection).
--   * T-07/T-08  ledger_prior_year: prior-year balance per ledger. The statements' comparative column
--           (prior_year_amounts, per template line) is derived from it.
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

ALTER TABLE exceptions ADD COLUMN is_above_materiality BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE exceptions SET is_above_materiality = TRUE WHERE source = 'materiality';

ALTER TABLE exceptions DROP CONSTRAINT exceptions_source_check;
ALTER TABLE exceptions ADD CONSTRAINT exceptions_source_check
  CHECK (source IN ('mapping', 'check', 'reconciliation', 'manual', 'materiality', 'gstr9c', 'udin'));

CREATE TABLE audit_pack_builds (
  id                 BIGSERIAL PRIMARY KEY,
  engagement_id      BIGINT NOT NULL UNIQUE REFERENCES engagements(id),
  built_at_revision  INT NOT NULL,
  built_by           VARCHAR(64) NOT NULL,
  built_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- (no lock trigger on purpose: building or exporting the pack changes no accounting data and is allowed after sign-off)
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON audit_pack_builds FOR EACH ROW EXECUTE FUNCTION audit_row_change();

CREATE TABLE ledger_prior_year (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  ledger_id     BIGINT NOT NULL,
  amount        NUMERIC(18,2) NOT NULL,                  -- the prior-year closing balance, Dr positive / Cr negative (like the trial balance)
  updated_by    BIGINT NOT NULL REFERENCES users(id),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (ledger_id),
  CONSTRAINT fk_lpy_ledger FOREIGN KEY (ledger_id, engagement_id) REFERENCES ledgers(id, engagement_id)
);
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_prior_year FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON ledger_prior_year FOR EACH ROW EXECUTE FUNCTION audit_row_change();
