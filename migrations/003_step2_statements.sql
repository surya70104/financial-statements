-- ============================================================================
-- 003_step2_statements.sql : Balance Sheet (T-07) and Profit & Loss (T-08)
--   * engagements.other_reserves_movement (input, default 0.00)
--   * prior_year_amounts: comparative per template line (seeded 0.00 for the pilot)
--   * group_line_defaults: Tally group -> Schedule III line (drives Auto-Map; data, not code)
--   * v_statement_line_totals: exact rupee totals per template line, from v_adjusted_tb only
-- ============================================================================
SELECT set_config('app.user_id', '1', true);          -- attribute seed rows to the SYSTEM user

ALTER TABLE engagements
  ADD COLUMN other_reserves_movement NUMERIC(18,2) NOT NULL DEFAULT 0;

-- Comparative (previous period) amount per detail line, stored as PRESENTED (Cr-natured lines positive).
CREATE TABLE prior_year_amounts (
  id            BIGSERIAL PRIMARY KEY,
  engagement_id BIGINT NOT NULL REFERENCES engagements(id),
  line_id       BIGINT NOT NULL REFERENCES template_lines(id),
  amount        NUMERIC(18,2) NOT NULL DEFAULT 0,
  UNIQUE (engagement_id, line_id)
);
CREATE TRIGGER trg_audit AFTER INSERT OR UPDATE OR DELETE ON prior_year_amounts
  FOR EACH ROW EXECUTE FUNCTION audit_row_change();

INSERT INTO prior_year_amounts (engagement_id, line_id, amount)
SELECT e.id, tl.id, 0.00
FROM engagements e JOIN template_lines tl ON tl.template_id = e.template_id AND tl.calc_kind = 'mapped';

-- Auto-map rules: one default Schedule III line per Tally group name, per template.
CREATE TABLE group_line_defaults (
  id          BIGSERIAL PRIMARY KEY,
  template_id BIGINT NOT NULL REFERENCES statement_templates(id),
  tally_group TEXT   NOT NULL,
  line_id     BIGINT NOT NULL REFERENCES template_lines(id),
  UNIQUE (template_id, tally_group)
);

INSERT INTO group_line_defaults (template_id, tally_group, line_id)
SELECT tl.template_id, d.grp, tl.id
FROM (VALUES
  ('Capital Account','BS.EQ.SC'),            ('Reserves & Surplus','BS.EQ.RS'),
  ('Secured Loans','BS.NCL.BORR'),           ('Unsecured Loans','BS.CL.BORR'),
  ('Sundry Creditors','BS.CL.TP'),           ('Duties & Taxes','BS.CL.OTH'),
  ('Provisions','BS.CL.PROV'),               ('Current Liabilities','BS.CL.OTH'),
  ('Deferred Tax','BS.NCL.DTL'),
  ('Fixed Assets','BS.NCA.PPE'),             ('Investments','BS.NCA.INV'),
  ('Deposits (Asset)','BS.NCA.OTH'),         ('Loans & Advances (Asset)','BS.CA.STLA'),
  ('Current Assets','BS.CA.OTH'),            ('Sundry Debtors','BS.CA.TR'),
  ('Bank Accounts','BS.CA.CASH'),            ('Cash-in-Hand','BS.CA.CASH'),
  ('Sales Accounts','PL.REV'),               ('Indirect Incomes','PL.OI'),
  ('Direct Expenses','PL.COGS'),             ('Indirect Expenses','PL.OTHEXP')
) AS d(grp, code)
JOIN statement_templates t ON t.code = 'SCH3-COMPANY'
JOIN template_lines tl ON tl.template_id = t.id AND tl.code = d.code;

-- Exact rupee total per DETAIL line, strictly from v_adjusted_tb (mapped ledgers only).
--   signed_balance = Dr positive / Cr negative, as stored
--   amount         = signed_balance x presentation_sign  (liabilities, equity and income show positive)
-- Subtotals / derived lines are computed from the template formulas in statement_service.
CREATE VIEW v_statement_line_totals AS
SELECT e.id                                                  AS engagement_id,
       tl.id                                                 AS line_id,
       tl.code, tl.statement, tl.section, tl.label, tl.sort_order, tl.presentation_sign,
       coalesce(sum(a.adjusted_balance), 0)::numeric(18,2)   AS signed_balance,
       (coalesce(sum(a.adjusted_balance), 0) * tl.presentation_sign)::numeric(18,2) AS amount,
       count(a.ledger_id)                                    AS ledger_count,
       coalesce(py.amount, 0)::numeric(18,2)                 AS py_amount
FROM engagements e
JOIN template_lines tl ON tl.template_id = e.template_id AND tl.calc_kind = 'mapped'
LEFT JOIN v_adjusted_tb a ON a.engagement_id = e.id AND a.line_id = tl.id
LEFT JOIN prior_year_amounts py ON py.engagement_id = e.id AND py.line_id = tl.id
GROUP BY e.id, tl.id, py.amount;
