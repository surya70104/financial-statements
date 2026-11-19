-- ============================================================================
-- 002_seed_step1.sql : roles/users, Private Limited Schedule III template, pilot engagement
-- ============================================================================
SELECT set_config('app.user_id', '1', true);          -- attribute seed changes to the SYSTEM user

-- Roles and users (Step 1 uses a dev role switcher; there are no real passwords yet) ---------------------
INSERT INTO roles (code, description) VALUES
  ('SYSTEM',  'Background jobs and migrations'),
  ('MAKER',   'Prepares: imports trial balance, maps ledgers, drafts adjustments'),
  ('CHECKER', 'Reviews: read-only in Step 1; approves adjustments and clears exceptions in later steps'),
  ('PARTNER', 'Engagement partner: full access, sign-off');

INSERT INTO users (id, email, full_name, password_hash) VALUES
  (1, 'system@caworkbench.local',  'System',                 '!'),
  (2, 'maker@caworkbench.local',   'Maya Maker (Preparer)',  '!'),
  (3, 'checker@caworkbench.local', 'Chetan Checker (Reviewer)', '!'),
  (4, 'partner@caworkbench.local', 'Priya Partner',          '!');
SELECT setval('users_id_seq', 4);

INSERT INTO user_roles (user_id, role_code) VALUES (1,'SYSTEM'), (2,'MAKER'), (3,'CHECKER'), (4,'PARTNER');

-- Exception causes ---------------------------------------------------------------------------------------------
INSERT INTO exception_causes (code, label, sort_order) VALUES
  ('UNMAPPED_LEDGER',  'Ledger not mapped to a Schedule III line and note', 10),
  ('CHECK_FAILURE',    'Checks engine failure',                             20),
  ('RECON_DIFFERENCE', 'Reconciliation difference',                         30),
  ('ABOVE_MATERIALITY','Difference above materiality',                      40),
  ('MISSING_UDIN',     'Signed report without UDIN',                        50),
  ('MANUAL',           'Raised manually',                                   90);

-- Import profiles (Q3 lives here: header aliases per logical field, all lower-case) ----------------------------
INSERT INTO import_profiles (code, name, format, column_map, is_default) VALUES
 ('TALLY_TB_CSV_V1',  'Tally Trial Balance (CSV)',  'csv',
  '{"ledger":["ledger name","particulars","ledger"],
    "group":["under group","group","parent","parent group"],
    "opening":["opening balance","opening"],
    "opening_type":["opening type","opening dr/cr","opening drcr"],
    "debit":["debit","debit amount","period debit","dr"],
    "credit":["credit","credit amount","period credit","cr"],
    "closing":["closing balance","closing"],
    "closing_type":["closing type","closing dr/cr","closing drcr"]}', TRUE),
 ('TALLY_TB_XLSX_V1', 'Tally Trial Balance (Excel)', 'xlsx',
  '{"ledger":["ledger name","particulars","ledger"],
    "group":["under group","group","parent","parent group"],
    "opening":["opening balance","opening"],
    "opening_type":["opening type","opening dr/cr","opening drcr"],
    "debit":["debit","debit amount","period debit","dr"],
    "credit":["credit","credit amount","period credit","cr"],
    "closing":["closing balance","closing"],
    "closing_type":["closing type","closing dr/cr","closing drcr"]}', TRUE);

-- Template: Schedule III, Private Limited company --------------------------------------------------------------
INSERT INTO statement_templates (code, name, entity_type) VALUES
  ('SCH3-COMPANY', 'Schedule III (Division I) - Private Limited Company', 'PRIVATE_LIMITED');

-- (code, statement, section, label, sort, kind, presentation_sign, note_no)
-- Cr-natured lines (equity, liabilities, income) carry presentation_sign = -1 so they present as positive.
CREATE TEMP TABLE _lines (code text, statement text, section text, label text, sort int, kind text, sign int, note_no text, formula jsonb);
INSERT INTO _lines VALUES
 ('BS.EQ.SC',        'BS','Equity',                    'Share capital',                                  110,'mapped',  -1,'1',  NULL),
 ('BS.EQ.RS',        'BS','Equity',                    'Reserves and surplus',                           120,'mapped',  -1,'2',  NULL),
 ('BS.EQ.TOTAL',     'BS','Equity',                    'Total shareholders'' funds',                     130,'subtotal', 1,NULL, '{"sum":["BS.EQ.SC","BS.EQ.RS"]}'),
 ('BS.NCL.BORR',     'BS','Non-current liabilities',   'Long-term borrowings',                           210,'mapped',  -1,'3',  NULL),
 ('BS.NCL.DTL',      'BS','Non-current liabilities',   'Deferred tax liabilities (net)',                 220,'mapped',  -1,'4',  NULL),
 ('BS.NCL.OTH',      'BS','Non-current liabilities',   'Other long-term liabilities',                    230,'mapped',  -1,'5',  NULL),
 ('BS.NCL.PROV',     'BS','Non-current liabilities',   'Long-term provisions',                           240,'mapped',  -1,'6',  NULL),
 ('BS.NCL.TOTAL',    'BS','Non-current liabilities',   'Total non-current liabilities',                  250,'subtotal', 1,NULL, '{"sum":["BS.NCL.BORR","BS.NCL.DTL","BS.NCL.OTH","BS.NCL.PROV"]}'),
 ('BS.CL.BORR',      'BS','Current liabilities',       'Short-term borrowings',                          310,'mapped',  -1,'7',  NULL),
 ('BS.CL.TP',        'BS','Current liabilities',       'Trade payables',                                 320,'mapped',  -1,'8',  NULL),
 ('BS.CL.OTH',       'BS','Current liabilities',       'Other current liabilities',                      330,'mapped',  -1,'9',  NULL),
 ('BS.CL.PROV',      'BS','Current liabilities',       'Short-term provisions',                          340,'mapped',  -1,'10', NULL),
 ('BS.CL.TOTAL',     'BS','Current liabilities',       'Total current liabilities',                      350,'subtotal', 1,NULL, '{"sum":["BS.CL.BORR","BS.CL.TP","BS.CL.OTH","BS.CL.PROV"]}'),
 ('BS.EQL.TOTAL',    'BS','Equity and liabilities',    'Total equity and liabilities',                   360,'subtotal', 1,NULL, '{"sum":["BS.EQ.TOTAL","BS.NCL.TOTAL","BS.CL.TOTAL"]}'),
 ('BS.NCA.PPE',      'BS','Non-current assets',        'Property, plant and equipment',                  410,'mapped',   1,'11', NULL),
 ('BS.NCA.INTANG',   'BS','Non-current assets',        'Intangible assets',                              420,'mapped',   1,'12', NULL),
 ('BS.NCA.INV',      'BS','Non-current assets',        'Non-current investments',                        430,'mapped',   1,'13', NULL),
 ('BS.NCA.DTA',      'BS','Non-current assets',        'Deferred tax assets (net)',                      440,'mapped',   1,'14', NULL),
 ('BS.NCA.LTLA',     'BS','Non-current assets',        'Long-term loans and advances',                   450,'mapped',   1,'15', NULL),
 ('BS.NCA.OTH',      'BS','Non-current assets',        'Other non-current assets',                       460,'mapped',   1,'16', NULL),
 ('BS.NCA.TOTAL',    'BS','Non-current assets',        'Total non-current assets',                       470,'subtotal', 1,NULL, '{"sum":["BS.NCA.PPE","BS.NCA.INTANG","BS.NCA.INV","BS.NCA.DTA","BS.NCA.LTLA","BS.NCA.OTH"]}'),
 ('BS.CA.INV',       'BS','Current assets',            'Current investments',                            510,'mapped',   1,'17', NULL),
 ('BS.CA.INVT',      'BS','Current assets',            'Inventories',                                    520,'mapped',   1,'18', NULL),
 ('BS.CA.TR',        'BS','Current assets',            'Trade receivables',                              530,'mapped',   1,'19', NULL),
 ('BS.CA.CASH',      'BS','Current assets',            'Cash and cash equivalents',                      540,'mapped',   1,'20', NULL),
 ('BS.CA.STLA',      'BS','Current assets',            'Short-term loans and advances',                  550,'mapped',   1,'21', NULL),
 ('BS.CA.OTH',       'BS','Current assets',            'Other current assets',                           560,'mapped',   1,'22', NULL),
 ('BS.CA.TOTAL',     'BS','Current assets',            'Total current assets',                           570,'subtotal', 1,NULL, '{"sum":["BS.CA.INV","BS.CA.INVT","BS.CA.TR","BS.CA.CASH","BS.CA.STLA","BS.CA.OTH"]}'),
 ('BS.ASSETS.TOTAL', 'BS','Assets',                    'Total assets',                                   580,'subtotal', 1,NULL, '{"sum":["BS.NCA.TOTAL","BS.CA.TOTAL"]}'),
 ('PL.REV',          'PL','Income',                    'Revenue from operations',                        610,'mapped',  -1,'23', NULL),
 ('PL.OI',           'PL','Income',                    'Other income',                                   620,'mapped',  -1,'24', NULL),
 ('PL.INCOME.TOTAL', 'PL','Income',                    'Total income',                                   630,'subtotal', 1,NULL, '{"sum":["PL.REV","PL.OI"]}'),
 ('PL.COGS',         'PL','Expenses',                  'Cost of materials and direct expenses',          710,'mapped',   1,'25', NULL),
 ('PL.EMP',          'PL','Expenses',                  'Employee benefits expense',                      720,'mapped',   1,'26', NULL),
 ('PL.FIN',          'PL','Expenses',                  'Finance costs',                                  730,'mapped',   1,'27', NULL),
 ('PL.DEP',          'PL','Expenses',                  'Depreciation and amortisation expense',          740,'mapped',   1,'28', NULL),
 ('PL.OTHEXP',       'PL','Expenses',                  'Other expenses',                                 750,'mapped',   1,'29', NULL),
 ('PL.EXP.TOTAL',    'PL','Expenses',                  'Total expenses',                                 760,'subtotal', 1,NULL, '{"sum":["PL.COGS","PL.EMP","PL.FIN","PL.DEP","PL.OTHEXP"]}'),
 ('PL.PBT',          'PL','Profit',                    'Profit / (loss) before tax',                     770,'derived',  1,NULL, '{"sub":["PL.INCOME.TOTAL","PL.EXP.TOTAL"]}'),
 ('PL.TAX',          'PL','Profit',                    'Tax expense (current and deferred)',             780,'mapped',   1,'30', NULL),
 ('PL.PAT',          'PL','Profit',                    'Profit / (loss) for the period',                 790,'derived',  1,NULL, '{"sub":["PL.PBT","PL.TAX"]}');

INSERT INTO template_lines (template_id, code, statement, section, label, sort_order, calc_kind, formula, presentation_sign, requires_note)
SELECT t.id, l.code, l.statement, l.section, l.label, l.sort, l.kind, l.formula, l.sign, (l.kind = 'mapped')
FROM _lines l CROSS JOIN statement_templates t WHERE t.code = 'SCH3-COMPANY';

-- One note per mapped line (Note numbers as in the Schedule III order above)
INSERT INTO template_notes (template_id, line_id, note_no, title, sort_order)
SELECT tl.template_id, tl.id, l.note_no, tl.label, l.note_no::int
FROM _lines l
JOIN statement_templates t ON t.code = 'SCH3-COMPANY'
JOIN template_lines tl ON tl.template_id = t.id AND tl.code = l.code
WHERE l.note_no IS NOT NULL;
DROP TABLE _lines;

-- Pilot client and engagement (Q2: the template is NOT confirmed yet, the UI shows a banner) ---------------------
INSERT INTO clients (name, entity_type) VALUES ('9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED', 'PRIVATE_LIMITED');

INSERT INTO engagements (client_id, fy_label, period_start, period_end, engagement_type, status, template_id, default_unit)
SELECT c.id, '2026-27', DATE '2026-04-01', DATE '2027-03-31', 'STATUTORY_AUDIT', 'planning', t.id, 'LAKH'
FROM clients c, statement_templates t
WHERE c.name = '9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED' AND t.code = 'SCH3-COMPANY';

INSERT INTO statement_versions (engagement_id, version_no, status, data_revision, created_by, created_reason)
SELECT id, 1, 'draft', 0, 1, 'initial' FROM engagements;

UPDATE engagements SET current_version_id = (SELECT id FROM statement_versions WHERE engagement_id = engagements.id AND version_no = 1);
