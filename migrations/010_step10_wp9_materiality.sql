-- ============================================================================
-- 010_step10_wp9_materiality.sql : T-23 Materiality
--   * engagement_materiality: one row per engagement (entered manually or calculated as a % of a benchmark).
--   * exceptions.source gains 'materiality' (year-on-year statement-line variances above materiality; cause ABOVE_MATERIALITY
--     already exists in exception_causes).
--   * Database backstop: no change to materiality once the engagement is locked by the Partner sign-off.
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

CREATE TABLE engagement_materiality (
  id                      BIGSERIAL PRIMARY KEY,
  engagement_id           BIGINT NOT NULL UNIQUE REFERENCES engagements(id),
  input_mode              TEXT NOT NULL CHECK (input_mode IN ('manual', 'calculated')),
  benchmark_basis         TEXT NOT NULL CHECK (benchmark_basis IN ('manual', 'revenue', 'total_assets', 'expenses')),
  benchmark_pct           NUMERIC(6,2),
  benchmark_amount        NUMERIC(18,2),
  overall_materiality     NUMERIC(18,2) NOT NULL CHECK (overall_materiality > 0),
  performance_materiality NUMERIC(18,2) CHECK (performance_materiality IS NULL OR performance_materiality > 0),
  trivial_threshold       NUMERIC(18,2) CHECK (trivial_threshold IS NULL OR trivial_threshold > 0),
  set_by                  VARCHAR(64) NOT NULL,
  set_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  notes                   TEXT,
  CONSTRAINT ck_materiality_mode CHECK (
    (input_mode = 'manual'     AND benchmark_basis = 'manual' AND benchmark_pct IS NULL AND benchmark_amount IS NULL)
 OR (input_mode = 'calculated' AND benchmark_basis <> 'manual' AND benchmark_pct > 0 AND benchmark_pct <= 100 AND benchmark_amount > 0))
);

CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON engagement_materiality
  FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON engagement_materiality
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();

ALTER TABLE exceptions DROP CONSTRAINT exceptions_source_check;
ALTER TABLE exceptions ADD CONSTRAINT exceptions_source_check
  CHECK (source IN ('mapping', 'check', 'reconciliation', 'manual', 'materiality'));
