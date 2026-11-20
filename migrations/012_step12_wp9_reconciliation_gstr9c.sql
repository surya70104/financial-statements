-- ============================================================================
-- 012_step12_wp9_reconciliation_gstr9c.sql : T-20 Reconciliation Engine and T-21 GSTR-9C Reconciliation
--   * reconciliation_items: differences between our books and an outside statement (bank, party, other). A row marked
--     'propose_adjustment' can be turned into a DRAFT journal entry; it reaches the adjusted TB only when a different user
--     approves it on the Adjustments tab (the Step 5 maker-checker flow, unchanged).
--   * gstr9c_reconciliations: books versus the GSTR-9C figure per GST head and table. Every non-zero difference is ONE
--     deduplicated issue in the Issue Tracker (exceptions.source = 'gstr9c').
--   * Both tables are frozen once the Partner has signed off (same trigger as the other engagement data).
-- ============================================================================
SELECT set_config('app.user_id', '1', true);

ALTER TABLE exceptions DROP CONSTRAINT exceptions_source_check;
ALTER TABLE exceptions ADD CONSTRAINT exceptions_source_check
  CHECK (source IN ('mapping', 'check', 'reconciliation', 'manual', 'materiality', 'gstr9c'));
INSERT INTO exception_causes (code, label, sort_order) VALUES ('GSTR9C_DIFFERENCE', 'GSTR-9C figure differs from the books', 45);

CREATE TABLE reconciliation_items (
  id                 BIGSERIAL PRIMARY KEY,
  engagement_id      BIGINT NOT NULL REFERENCES engagements(id),
  recon_type         TEXT NOT NULL CHECK (recon_type IN ('bank', 'party', 'other')),
  account_name       TEXT NOT NULL CHECK (length(btrim(account_name)) > 0),
  item_date          DATE,
  reference_no       TEXT,
  description        TEXT NOT NULL CHECK (length(btrim(description)) > 0),
  book_amount        NUMERIC(18,2) NOT NULL,
  external_amount    NUMERIC(18,2) NOT NULL,
  difference_amount  NUMERIC(18,2) NOT NULL,
  adjustment_action  TEXT NOT NULL CHECK (adjustment_action IN ('timing_only', 'propose_adjustment')),
  ledger_code        TEXT,                                -- the other side of the entry: expense / income / party ledger
  offset_ledger_code TEXT,                                -- the bank / control ledger the difference is measured on
  status             TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'proposed', 'resolved')),
  proposed_journal_id BIGINT REFERENCES journal_entries(id),
  created_by         BIGINT NOT NULL REFERENCES users(id),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_recon_difference CHECK (difference_amount = external_amount - book_amount),
  CONSTRAINT ck_recon_ledgers CHECK (adjustment_action = 'timing_only' OR difference_amount = 0
                                     OR (ledger_code IS NOT NULL AND offset_ledger_code IS NOT NULL)),
  CONSTRAINT ck_recon_open CHECK (status <> 'open' OR proposed_journal_id IS NULL),
  CONSTRAINT ck_recon_proposed CHECK (status <> 'proposed' OR proposed_journal_id IS NOT NULL)
);
CREATE UNIQUE INDEX ux_recon_no_duplicate ON reconciliation_items
  (engagement_id, recon_type, lower(btrim(account_name)), coalesce(item_date, DATE '1900-01-01'), lower(btrim(coalesce(reference_no, ''))), book_amount, external_amount);
CREATE INDEX ix_recon_eng ON reconciliation_items (engagement_id, status);

CREATE TABLE gstr9c_reconciliations (
  id                BIGSERIAL PRIMARY KEY,
  engagement_id     BIGINT NOT NULL REFERENCES engagements(id),
  gst_head          TEXT NOT NULL CHECK (gst_head IN ('CGST', 'SGST', 'IGST', 'CESS')),
  table_ref         TEXT NOT NULL CHECK (length(btrim(table_ref)) > 0),          -- e.g. 'Table 9', 'Table 12'
  description       TEXT,
  books_amount      NUMERIC(18,2) NOT NULL,
  gstr9c_amount     NUMERIC(18,2) NOT NULL,
  difference_amount NUMERIC(18,2) NOT NULL,
  status            TEXT NOT NULL CHECK (status IN ('matched', 'difference_logged', 'resolved')),
  created_by        BIGINT NOT NULL REFERENCES users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (engagement_id, gst_head, table_ref),
  CONSTRAINT ck_gstr_difference CHECK (difference_amount = books_amount - gstr9c_amount),
  CONSTRAINT ck_gstr_status CHECK ((status = 'difference_logged' AND difference_amount <> 0) OR (status IN ('matched', 'resolved') AND difference_amount = 0))
);

-- A reconciliation row moves open -> proposed (a draft journal exists) -> resolved (that journal was approved); nothing else changes.
-- Rows still open (or timing-only ones that never needed an entry) can be deleted; a row with a journal is permanent.
CREATE FUNCTION reconciliation_items_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.proposed_journal_id IS NOT NULL THEN RAISE EXCEPTION 'A reconciliation item with a journal entry is permanent'; END IF;
    RETURN OLD;
  END IF;
  IF (OLD.status = 'open' AND NEW.status = 'proposed') OR (OLD.status = 'proposed' AND NEW.status = 'resolved') THEN
    IF (NEW.engagement_id, NEW.recon_type, NEW.account_name, NEW.item_date, NEW.reference_no, NEW.description, NEW.book_amount, NEW.external_amount,
        NEW.difference_amount, NEW.adjustment_action, NEW.ledger_code, NEW.offset_ledger_code, NEW.created_by)
       IS NOT DISTINCT FROM
       (OLD.engagement_id, OLD.recon_type, OLD.account_name, OLD.item_date, OLD.reference_no, OLD.description, OLD.book_amount, OLD.external_amount,
        OLD.difference_amount, OLD.adjustment_action, OLD.ledger_code, OLD.offset_ledger_code, OLD.created_by) THEN
      RETURN NEW;
    END IF;
  END IF;
  RAISE EXCEPTION 'A reconciliation item cannot be edited; delete it (if it has no journal) and add it again';
END $$;
CREATE TRIGGER trg_recon_guard BEFORE INSERT OR UPDATE OR DELETE ON reconciliation_items FOR EACH ROW EXECUTE FUNCTION reconciliation_items_guard();

CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON reconciliation_items   FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_locked_guard BEFORE INSERT OR UPDATE OR DELETE ON gstr9c_reconciliations FOR EACH ROW EXECUTE FUNCTION block_if_engagement_locked();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON reconciliation_items   FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON gstr9c_reconciliations FOR EACH ROW EXECUTE FUNCTION audit_row_change();
