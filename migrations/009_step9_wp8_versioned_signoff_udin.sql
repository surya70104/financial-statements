-- ============================================================================
-- 009_step9_wp8_versioned_signoff_udin.sql : Version-locked report sign-off (T-17) and UDIN dashboard (T-18)
--   * report_signoff_versions: one immutable row per Partner sign-off (v1, v2, ...). The only change ever allowed is
--     signed_locked -> superseded, filling reopened_by / reopened_at / reopen_reason (a formal Partner re-opening).
--   * engagement_signoffs stays the single "lock state" row per engagement (locking.py and every lock trigger read it).
--     It may now be updated ONLY inside a signed-off/reopen transaction (the service sets app.signoff_transition);
--     any other UPDATE and every DELETE is still refused.
--   * udin_records: exactly one UDIN slot per signed version (pending_udin -> recorded -> revoked), strict ICAI format.
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

CREATE TABLE report_signoff_versions (
  id              BIGSERIAL PRIMARY KEY,
  engagement_id   BIGINT NOT NULL REFERENCES engagements(id),
  version_number  INT    NOT NULL CHECK (version_number >= 1),
  status          TEXT   NOT NULL DEFAULT 'signed_locked' CHECK (status IN ('signed_locked', 'superseded')),
  signed_off_by   BIGINT NOT NULL REFERENCES users(id),
  signed_off_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  signoff_note    TEXT,
  snapshot_json   JSONB  NOT NULL,                       -- frozen totals + the whole audit pack at the moment of sign-off
  reopened_by     BIGINT REFERENCES users(id),
  reopened_at     TIMESTAMPTZ,
  reopen_reason   TEXT,
  UNIQUE (engagement_id, version_number),
  CONSTRAINT ck_reopen_fields CHECK (
    (status = 'signed_locked' AND reopened_by IS NULL AND reopened_at IS NULL AND reopen_reason IS NULL)
 OR (status = 'superseded' AND reopened_by IS NOT NULL AND reopened_at IS NOT NULL AND length(btrim(reopen_reason)) > 0))
);
-- at most one version can be the live, locked one
CREATE UNIQUE INDEX ux_one_active_version ON report_signoff_versions (engagement_id) WHERE status = 'signed_locked';

CREATE FUNCTION report_signoff_versions_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'A signed report version cannot be changed or deleted'; END IF;
  IF OLD.status = 'signed_locked' AND NEW.status = 'superseded'
     AND (NEW.id, NEW.engagement_id, NEW.version_number, NEW.signed_off_by, NEW.signed_off_at, NEW.signoff_note)
         IS NOT DISTINCT FROM (OLD.id, OLD.engagement_id, OLD.version_number, OLD.signed_off_by, OLD.signed_off_at, OLD.signoff_note)
     AND NEW.snapshot_json = OLD.snapshot_json THEN
    RETURN NEW;                                            -- the one permitted transition: formal re-opening
  END IF;
  RAISE EXCEPTION 'A signed report version cannot be changed or deleted (only signed_locked -> superseded on re-opening)';
END $$;
CREATE TRIGGER trg_version_guard BEFORE UPDATE OR DELETE ON report_signoff_versions
  FOR EACH ROW EXECUTE FUNCTION report_signoff_versions_guard();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON report_signoff_versions
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();

-- engagement_signoffs: replace the Step 6 "always immutable" rule with "only the sign-off / re-open transaction may update"
CREATE OR REPLACE FUNCTION engagement_signoffs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND current_setting('app.signoff_transition', true) = 'on' THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'A Partner sign-off record cannot be changed or deleted';
END $$;

-- Backfill: every existing sign-off becomes version 1
INSERT INTO report_signoff_versions (engagement_id, version_number, status, signed_off_by, signed_off_at, signoff_note, snapshot_json)
SELECT s.engagement_id, 1, 'signed_locked',
       s.signed_off_by, s.signed_off_at, s.signoff_note, s.snapshot_json
  FROM engagement_signoffs s;

CREATE TABLE udin_records (
  id                  BIGSERIAL PRIMARY KEY,
  engagement_id       BIGINT NOT NULL REFERENCES engagements(id),
  signoff_version_id  BIGINT NOT NULL UNIQUE REFERENCES report_signoff_versions(id),
  udin_number         VARCHAR(18) UNIQUE,
  document_type       TEXT NOT NULL DEFAULT 'Statutory Audit Report',
  udin_generated_date DATE,
  status              TEXT NOT NULL DEFAULT 'pending_udin' CHECK (status IN ('pending_udin', 'recorded', 'revoked')),
  recorded_by         BIGINT REFERENCES users(id),
  recorded_at         TIMESTAMPTZ,
  revoked_by          BIGINT REFERENCES users(id),
  revoked_at          TIMESTAMPTZ,
  revoked_reason      TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_udin_format CHECK (udin_number IS NULL OR udin_number ~ '^[A-Z0-9]{18}$'),
  CONSTRAINT ck_udin_state CHECK (
    (status = 'pending_udin' AND udin_number IS NULL AND recorded_at IS NULL AND revoked_at IS NULL)
 OR (status = 'recorded'     AND udin_number IS NOT NULL AND udin_generated_date IS NOT NULL AND recorded_by IS NOT NULL AND revoked_at IS NULL)
 OR (status = 'revoked'      AND udin_number IS NOT NULL AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0))
);

CREATE FUNCTION udin_records_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'A UDIN record cannot be deleted'; END IF;
  IF TG_OP = 'INSERT' THEN
    IF NOT EXISTS (SELECT 1 FROM report_signoff_versions WHERE id = NEW.signoff_version_id AND engagement_id = NEW.engagement_id) THEN
      RAISE EXCEPTION 'The signed version does not belong to this engagement';
    END IF;
    RETURN NEW;
  END IF;
  IF (NEW.engagement_id, NEW.signoff_version_id) IS DISTINCT FROM (OLD.engagement_id, OLD.signoff_version_id) THEN
    RAISE EXCEPTION 'A UDIN record cannot be moved to another version';
  END IF;
  IF (OLD.status = 'pending_udin' AND NEW.status = 'recorded') OR (OLD.status = 'recorded' AND NEW.status = 'revoked') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Invalid UDIN status change % -> %', OLD.status, NEW.status;
END $$;
CREATE TRIGGER trg_udin_guard BEFORE INSERT OR UPDATE OR DELETE ON udin_records
  FOR EACH ROW EXECUTE FUNCTION udin_records_guard();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON udin_records
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();

INSERT INTO udin_records (engagement_id, signoff_version_id)
SELECT engagement_id, id FROM report_signoff_versions;
