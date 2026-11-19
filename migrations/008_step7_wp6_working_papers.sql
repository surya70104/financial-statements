-- ============================================================================
-- 008_step7_wp6_working_papers.sql : Working paper links on statement lines and notes (T-14)
--   * target_code: the template line code (e.g. BS.CA.TR) for a statement line, or NOTE-<note no> (e.g. NOTE-20) for a note
--   * status attached -> reviewed (maker-checker: the reviewer is never the person who attached it)
--   * reviewed links are immutable and cannot be deleted; no write at all once the engagement is signed off and locked
-- ============================================================================
CREATE TABLE working_paper_links (
  id             BIGSERIAL PRIMARY KEY,
  engagement_id  BIGINT NOT NULL REFERENCES engagements(id),
  target_type    TEXT   NOT NULL CHECK (target_type IN ('statement_line','note')),
  target_code    TEXT   NOT NULL,
  wp_ref_code    TEXT   NOT NULL CHECK (length(btrim(wp_ref_code)) > 0),
  wp_title       TEXT   NOT NULL CHECK (length(btrim(wp_title)) > 0),
  wp_url_or_path TEXT,
  status         TEXT   NOT NULL DEFAULT 'attached' CHECK (status IN ('attached','reviewed')),
  attached_by    BIGINT NOT NULL REFERENCES users(id),
  attached_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_by    BIGINT REFERENCES users(id),
  reviewed_at    TIMESTAMPTZ,
  notes          TEXT,
  CONSTRAINT ck_target_code_shape CHECK (
    (target_type = 'note' AND target_code ~ '^NOTE-[0-9]+$') OR (target_type = 'statement_line' AND target_code !~ '^NOTE-')),
  CONSTRAINT ck_review_complete CHECK (
    (status = 'attached' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR
    (status = 'reviewed' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_by <> attached_by)),
  CONSTRAINT uq_wp_per_target UNIQUE (engagement_id, target_type, target_code, wp_ref_code)
);
CREATE INDEX ix_wp_target ON working_paper_links (engagement_id, target_type, target_code);

CREATE FUNCTION working_paper_reviewed_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = 'reviewed' THEN
    RAISE EXCEPTION 'A reviewed working paper link is immutable (id %)', OLD.id;
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER trg_wp_reviewed_guard BEFORE UPDATE OR DELETE ON working_paper_links
  FOR EACH ROW EXECUTE FUNCTION working_paper_reviewed_guard();

-- Engagement lock (same function as Step 6): no create, review or delete after the Partner's sign-off.
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON working_paper_links
  FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON working_paper_links
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();
