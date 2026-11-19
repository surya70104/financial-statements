-- ============================================================================
-- 007_step6_wp5_issues_signoff.sql : Unified issue tracker (T-12) and hard sign-off gate (T-13)
--   * exceptions already supports source mapping/check/manual and status open/cleared (+ cleared_by, cleared_at, and
--     clear_reason, which the API exposes as "resolution_note"). Added: created_by (who raised a manual issue).
--   * engagement_signoffs: one immutable row per engagement. is_locked = TRUE freezes the engagement's data.
--   * Database backstop: no write to ledgers, balances, mappings, groups, comparatives or journals while locked.
--     (The services check first and answer HTTP 409; this trigger guarantees nothing slips past them.)
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

ALTER TABLE exceptions ADD COLUMN created_by BIGINT REFERENCES users(id);

CREATE TABLE engagement_signoffs (
  id             BIGSERIAL PRIMARY KEY,
  engagement_id  BIGINT NOT NULL UNIQUE REFERENCES engagements(id),
  signed_off_by  BIGINT NOT NULL REFERENCES users(id),
  signed_off_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  signoff_note   TEXT,
  snapshot_json  JSONB NOT NULL,                -- final statement totals, check results and revision at the moment of sign-off
  is_locked      BOOLEAN NOT NULL DEFAULT TRUE
);

-- A sign-off record is permanent. (Re-opening / versioned sign-off arrives with WP8 and will replace this rule deliberately.)
CREATE FUNCTION engagement_signoffs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'A Partner sign-off record cannot be changed or deleted'; END $$;
CREATE TRIGGER trg_signoff_immutable BEFORE UPDATE OR DELETE ON engagement_signoffs
  FOR EACH ROW EXECUTE FUNCTION engagement_signoffs_immutable();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON engagement_signoffs
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();

CREATE FUNCTION block_if_engagement_locked() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_eng bigint := coalesce(NEW.engagement_id, OLD.engagement_id);
BEGIN
  IF EXISTS (SELECT 1 FROM engagement_signoffs WHERE engagement_id = v_eng AND is_locked) THEN
    RAISE EXCEPTION 'Engagement is locked after Partner sign-off (% on %)', TG_OP, TG_TABLE_NAME;
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;

CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON tally_groups        FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON ledgers             FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_balances     FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON ledger_mappings     FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON prior_year_amounts  FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON journal_entries     FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON journal_lines       FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
