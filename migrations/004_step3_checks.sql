-- ============================================================================
-- 004_step3_checks.sql : Checks engine (T-11)
--   check_definitions (what to check, data), check_runs (one per trigger), check_results (one per check per run)
--   CHK-03 is seeded OFF until Notes (T-09, Step 4); CHK-06 is active and FAILS until open question Q1 is answered.
-- ============================================================================
CREATE TABLE check_definitions (
  code              TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  description       TEXT NOT NULL,
  rule_type         TEXT NOT NULL,               -- key into the registry in app/services/checks_service.py
  rule_config       JSONB NOT NULL DEFAULT '{}',
  definition_status TEXT NOT NULL DEFAULT 'confirmed' CHECK (definition_status IN ('confirmed','pending_definition')),
  is_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  enabled_from_step INT,
  sort_order        INT NOT NULL
);

CREATE TABLE check_runs (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  data_revision INT    NOT NULL,
  trigger_event TEXT   NOT NULL,                 -- TB_IMPORTED, MAPPING_CHANGED, AUTO_MAP, MANUAL, ...
  triggered_by  BIGINT NOT NULL REFERENCES users(id),
  run_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  passed_count  INT NOT NULL,                    -- among ACTIVE (enabled) checks
  total_count   INT NOT NULL,                    -- number of ACTIVE checks; a pending-definition check counts as not passing
  CHECK (passed_count BETWEEN 0 AND total_count)
);
CREATE INDEX ix_check_runs_latest ON check_runs (engagement_id, id DESC);

CREATE TABLE check_results (
  id         BIGSERIAL PRIMARY KEY,
  run_id     BIGINT NOT NULL REFERENCES check_runs(id),
  check_code TEXT   NOT NULL REFERENCES check_definitions(code),
  status     TEXT   NOT NULL CHECK (status IN ('pass','fail','not_run')),
  lhs_amount NUMERIC(18,2),
  rhs_amount NUMERIC(18,2),
  variance   NUMERIC(18,2) GENERATED ALWAYS AS (lhs_amount - rhs_amount) STORED,   -- exact difference
  detail     JSONB NOT NULL DEFAULT '{}',
  UNIQUE (run_id, check_code),
  CONSTRAINT ck_pass_means_zero_variance CHECK (status <> 'pass' OR lhs_amount IS NULL OR lhs_amount = rhs_amount)
);

-- check_runs / check_results are history: no edits, no deletes.
CREATE FUNCTION check_history_block_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_NAME; END $$;
CREATE TRIGGER trg_check_runs_immutable BEFORE UPDATE OR DELETE ON check_runs
  FOR EACH ROW EXECUTE FUNCTION check_history_block_mutation();
CREATE TRIGGER trg_check_results_immutable BEFORE UPDATE OR DELETE ON check_results
  FOR EACH ROW EXECUTE FUNCTION check_history_block_mutation();

INSERT INTO check_definitions (code, name, description, rule_type, rule_config, definition_status, is_enabled, enabled_from_step, sort_order) VALUES
 ('CHK-01', 'Balance Sheet balances',
  'Total assets = Total equity and liabilities (exact to the paisa).',
  'bs_balance', '{}', 'confirmed', TRUE, NULL, 10),
 ('CHK-02', 'Profit ties to reserves',
  'Reserves closing - Reserves opening - other reserves movement = Profit / (loss) for the period.',
  'profit_ties_reserves', '{"reserves_line":"BS.EQ.RS"}', 'confirmed', TRUE, NULL, 20),
 ('CHK-03', 'Notes tie to statement lines',
  'Each note total equals its Balance Sheet / P&L line. Switched on once Notes (T-09) are built in Step 4.',
  'notes_tie', '{}', 'confirmed', FALSE, 4, 30),
 ('CHK-04', 'Mapping completeness',
  'Unmapped active ledgers = 0.',
  'mapping_complete', '{}', 'confirmed', TRUE, NULL, 40),
 ('CHK-05', 'Comparatives present',
  'A previous-period figure is set up for every detail line of the statements (zero is a valid figure).',
  'comparatives_present', '{}', 'confirmed', TRUE, NULL, 50),
 ('CHK-06', 'Sixth check (open question Q1)',
  'Definition pending Product Owner confirmation (Q1). Counts as NOT passing and blocks sign-off until defined.',
  'pending_definition', '{"message":"Pending Product Owner confirmation on Q1"}', 'pending_definition', TRUE, NULL, 60);

-- Latest run per engagement, flattened (used by the sign-off blockers in a later step).
CREATE VIEW v_latest_check_results AS
SELECT r.engagement_id, r.id AS run_id, r.run_at, r.data_revision, cr.check_code, cr.status,
       cr.lhs_amount, cr.rhs_amount, cr.variance, cr.detail
FROM check_runs r JOIN check_results cr ON cr.run_id = r.id
WHERE r.id = (SELECT max(x.id) FROM check_runs x WHERE x.engagement_id = r.engagement_id);
