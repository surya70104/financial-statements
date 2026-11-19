-- ============================================================================
-- 005_step4_notes_ratios.sql : Notes (T-09) and Ratios (T-10)
--   * ratio_definitions: what each ratio is (display formula, unit, structured calculation key). All PROVISIONAL (Q7).
--   * CHK-03 (notes tie to statement lines) is switched ON now that Notes exist.
-- ============================================================================
CREATE TABLE ratio_definitions (
  code               TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  category           TEXT NOT NULL,                 -- Liquidity | Solvency | Profitability | Efficiency
  formula_display    TEXT NOT NULL,                 -- shown beside the ratio on screen (T-10)
  calc_key           TEXT NOT NULL,                 -- key into the registry in app/services/ratios_service.py
  unit               TEXT NOT NULL CHECK (unit IN ('times','%')),
  is_provisional     BOOLEAN NOT NULL DEFAULT TRUE, -- TRUE until open question Q7 (ratio list/definitions) is confirmed
  sort_order         INT NOT NULL
);

INSERT INTO ratio_definitions (code, name, category, formula_display, calc_key, unit, is_provisional, sort_order) VALUES
 ('R-01', 'Current Ratio',                      'Liquidity',     'Current assets / Current liabilities',                                   'current_ratio',        'times', TRUE, 10),
 ('R-02', 'Debt-Equity Ratio',                  'Solvency',      'Total borrowings / Shareholders'' equity',                               'debt_equity',          'times', TRUE, 20),
 ('R-03', 'Debt Service Coverage Ratio',        'Solvency',      '(Profit for the period + Depreciation + Finance costs) / Finance costs', 'dscr',                 'times', TRUE, 30),
 ('R-04', 'Return on Equity Ratio',             'Profitability', 'Profit for the period / Average shareholders'' equity',                  'roe',                  '%',     TRUE, 40),
 ('R-05', 'Trade Receivables Turnover Ratio',   'Efficiency',    'Revenue from operations / Average trade receivables',                    'receivables_turnover', 'times', TRUE, 50),
 ('R-06', 'Trade Payables Turnover Ratio',      'Efficiency',    'Cost of materials and direct expenses / Average trade payables',         'payables_turnover',    'times', TRUE, 60),
 ('R-07', 'Net Capital Turnover Ratio',         'Efficiency',    'Revenue from operations / (Current assets - Current liabilities)',       'net_capital_turnover', 'times', TRUE, 70),
 ('R-08', 'Net Profit Ratio',                   'Profitability', 'Profit for the period / Revenue from operations',                        'net_profit',           '%',     TRUE, 80),
 ('R-09', 'Return on Capital Employed',         'Profitability', 'Earnings before interest and tax / Capital employed',                    'roce',                 '%',     TRUE, 90);

-- T-11: the notes-tie check is switched on once T-09 is complete.
UPDATE check_definitions
   SET is_enabled = TRUE,
       description = 'Each note total equals its Balance Sheet / P&L line, to the paisa. The variance is the sum of the absolute differences.'
 WHERE code = 'CHK-03';
