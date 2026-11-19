-- ============================================================================
-- 006_step5_adjustments.sql : Adjustments (T-04), Approval and immutability (T-05), Adjusted trial balance (T-06)
--   journal_entries / journal_lines, database-level integrity, and v_adjusted_tb now = imported closing
--   + net of APPROVED journal lines only (debit +, credit -). Drafts never reach statements, notes, ratios or checks.
-- ============================================================================
CREATE TABLE journal_entries (
  id                BIGSERIAL PRIMARY KEY,
  engagement_id     BIGINT NOT NULL REFERENCES engagements(id),
  entry_number      INT    NOT NULL,
  entry_date        DATE   NOT NULL,
  description       TEXT   NOT NULL CHECK (length(btrim(description)) > 0),
  status            TEXT   NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
  created_by        BIGINT NOT NULL REFERENCES users(id),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_by       BIGINT REFERENCES users(id),
  approved_at       TIMESTAMPTZ,
  reverses_entry_id BIGINT REFERENCES journal_entries(id),
  UNIQUE (engagement_id, entry_number),
  UNIQUE (id, engagement_id),
  -- Maker-checker, enforced by the database as well as the API: an approved entry has an approver who is not its creator.
  CONSTRAINT ck_approval_complete CHECK (
    (status = 'draft'    AND approved_by IS NULL AND approved_at IS NULL) OR
    (status = 'approved' AND approved_by IS NOT NULL AND approved_at IS NOT NULL AND approved_by <> created_by)),
  CONSTRAINT ck_no_self_reversal CHECK (reverses_entry_id IS NULL OR reverses_entry_id <> id)
);
-- An entry can be reversed at most once (a pending reversal draft counts).
CREATE UNIQUE INDEX ux_one_reversal_per_entry ON journal_entries (reverses_entry_id) WHERE reverses_entry_id IS NOT NULL;

CREATE TABLE journal_lines (
  id               BIGSERIAL PRIMARY KEY,
  journal_entry_id BIGINT NOT NULL,
  engagement_id    BIGINT NOT NULL,                -- repeated from the entry so a line can only use this engagement's ledgers
  ledger_id        BIGINT NOT NULL,
  debit_amount     NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (debit_amount  >= 0),
  credit_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
  line_note        TEXT,
  CONSTRAINT fk_line_entry  FOREIGN KEY (journal_entry_id, engagement_id) REFERENCES journal_entries(id, engagement_id),
  CONSTRAINT fk_line_ledger FOREIGN KEY (ledger_id, engagement_id)        REFERENCES ledgers(id, engagement_id),
  -- exactly one side carries an amount: at least one > 0, never both > 0
  CONSTRAINT ck_one_side CHECK ((debit_amount > 0 AND credit_amount = 0) OR (credit_amount > 0 AND debit_amount = 0))
);
CREATE INDEX ix_journal_lines_entry  ON journal_lines (journal_entry_id);
CREATE INDEX ix_journal_lines_ledger ON journal_lines (ledger_id);

-- Immutability (T-05): an approved entry and its lines can never be changed, deleted or extended.
CREATE FUNCTION journal_entries_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_lines int; v_dr numeric; v_cr numeric;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status = 'approved' THEN RAISE EXCEPTION 'Approved journal entry % is immutable; post a reversal instead', OLD.id; END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'approved' THEN
    RAISE EXCEPTION 'Approved journal entry % is immutable; post a reversal instead', OLD.id;
  END IF;
  IF NEW.status = 'approved' THEN                  -- draft -> approved: must be a real, balanced entry
    SELECT count(*), coalesce(sum(debit_amount), 0), coalesce(sum(credit_amount), 0) INTO v_lines, v_dr, v_cr
      FROM journal_lines WHERE journal_entry_id = NEW.id;
    IF v_lines < 2 THEN RAISE EXCEPTION 'Entry % needs at least two lines', NEW.id; END IF;
    IF v_dr <> v_cr THEN RAISE EXCEPTION 'Entry % is not balanced (debit % vs credit %)', NEW.id, v_dr, v_cr; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_je_guard BEFORE UPDATE OR DELETE ON journal_entries
  FOR EACH ROW EXECUTE FUNCTION journal_entries_guard();

CREATE FUNCTION journal_lines_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_entry bigint := coalesce(NEW.journal_entry_id, OLD.journal_entry_id); v_status text;
BEGIN
  SELECT status INTO v_status FROM journal_entries WHERE id = v_entry;
  IF v_status = 'approved' THEN RAISE EXCEPTION 'Lines of approved journal entry % are immutable', v_entry; END IF;
  IF TG_OP = 'UPDATE' AND NEW.journal_entry_id <> OLD.journal_entry_id THEN
    RAISE EXCEPTION 'A journal line cannot be moved to another entry';
  END IF;
  RETURN coalesce(NEW, OLD);
END $$;
CREATE TRIGGER trg_jl_guard BEFORE INSERT OR UPDATE OR DELETE ON journal_lines
  FOR EACH ROW EXECUTE FUNCTION journal_lines_guard();

CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON journal_entries FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON journal_lines   FOR EACH ROW EXECUTE FUNCTION audit_row_change();

-- Adjusted trial balance (T-06). Same columns and types as the Step 1 view: statements, notes, ratios and
-- checks read it unchanged. Only APPROVED lines count.
CREATE OR REPLACE VIEW v_adjusted_tb AS
SELECT l.engagement_id,
       l.id                                   AS ledger_id,
       l.name                                 AS ledger_name,
       g.name                                 AS tally_group,
       g.nature                               AS tally_nature,
       coalesce(lb.closing_balance, 0)        AS tb_balance,
       coalesce(adj.net, 0)::numeric(18,2)    AS adjustment_net,
       coalesce(lb.closing_balance, 0) + coalesce(adj.net, 0) AS adjusted_balance,
       lb.py_closing_balance                  AS py_balance,
       m.line_id,
       m.note_id
FROM ledgers l
LEFT JOIN tally_groups    g  ON g.id = l.group_id
LEFT JOIN ledger_balances lb ON lb.ledger_id = l.id
LEFT JOIN ledger_mappings m  ON m.ledger_id = l.id
LEFT JOIN (SELECT jl.ledger_id, sum(jl.debit_amount - jl.credit_amount) AS net
             FROM journal_lines jl JOIN journal_entries je ON je.id = jl.journal_entry_id
            WHERE je.status = 'approved'
            GROUP BY jl.ledger_id) adj ON adj.ledger_id = l.id
WHERE l.is_active;
