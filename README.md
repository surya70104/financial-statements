# CA Workbench: Financial Statements and Connected Modules

A local web app for Chartered Accountants to turn a Tally trial balance into Schedule III financial statements, with adjustments, checks, working papers, an Audit Committee pack, versioned Partner sign-off, and the connected modules (materiality, TDS/TCS, fixed deposits, reconciliation, GSTR-9C and LFAR).

Pilot client: **9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED**, FY 2026-27, Statutory Audit. The spec is `Financial_Statements_Developer_Task_Brief.docx` (not in this repo).

**Status:** complete. Pass 1 (Steps 1-5), Pass 2 (Steps 6-9), Pass 3 (Steps 10-13, the WP9 connected modules), Step 14 (acceptance-criteria gaps closed) and Step 15 (the Product Owner's answers to Q1, Q2, Q4 and Q7 implemented). Every open question in the brief is now resolved.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Database | PostgreSQL 16, `psycopg` 3 with plain SQL (no ORM) |
| Frontend | Server-rendered Jinja2 pages with vanilla JavaScript (no framework, no build step) |
| Money | `NUMERIC(18,2)` rupees in the database and exact string decimals in the API. The shared `money.js` handles display (Lakhs or Rupees, Indian grouping, negatives in brackets) and exact paise comparisons. |
| Files | `openpyxl` reads `.xlsx` uploads (`.csv` is also accepted); `python-docx` writes the Word (.docx) exports |
| Tests | pytest (against a throwaway `<db>_test` database) and a Node test for `money.js` |

Core rules: every figure is calculated from the adjusted trial balance and never typed in. The audit trail (`audit_logs`) is append-only and written by database triggers plus business events. Roles are enforced through the `X-User-Id` header. Once the Partner signs off, the engagement is locked in both the services (HTTP 409) and the database. Connected modules post through the same approved-journal path as ordinary adjustments, so the statements, checks and notes always follow.

## What is built

### Pass 1: Steps 1-5
| Step | Scope |
|---|---|
| 1 | Schema and seed. Tally trial balance import (76 pilot ledgers). Mapping of ledgers to Schedule III lines with auto-map. Mapping exceptions. Audit trail. |
| 2 | Balance Sheet and Profit and Loss from mapped ledgers, with prior-year comparatives and a balance check. |
| 3 | Checks engine (CHK-01 to CHK-06) that re-runs after every change, with a Checks tab and badge. |
| 4 | Notes to the financial statements (30 notes) that tie to statement lines, and the Ratios tab with formulas shown. |
| 5 | Post adjustments, with maker-checker approval, reversals and immutability, and the adjusted trial balance. |

### Pass 2: Steps 6-9
| Step | Scope |
|---|---|
| 6 | Unified Issue Tracker (mapping, check and manual issues). Hard sign-off gate: Partner only, zero failing checks and zero open issues. Engagement lock. |
| 7 | Working paper links on the 30 statement lines and 30 notes, with reviewer-not-attacher rule and an evidence register. |
| 8 | Print-ready statements (browser Print / Save PDF with a statutory header), the Audit Committee / IA Pack, and a read-only Audit Trail viewer. |
| 9 | Version-locked sign-off (v1, v2, ...) with a formal Partner reopen, and the UDIN dashboard (18-character ICAI UDIN per signed version). |

### Pass 3: Steps 10-13 (WP9 connected modules)
| Step | Task | Scope |
|---|---|---|
| 10 | T-23 Materiality | Overall materiality entered manually or calculated as a percentage of revenue, total assets or expenses (exact paise), with performance (75%) and clearly trivial (5%) defaults. Checker or Partner sets it. Statement lines whose year-on-year change is above it become one deduplicated issue each and clear themselves. Shown in the header. |
| 11 | T-19 TDS / TCS pack | Register TDS and TCS (payable and receivable) from Excel, CSV or a form; a Checker or Partner posts each row as one approved journal (payables to other current liabilities, receivables to other current assets). |
| 11 | T-22 FD register | Register bank fixed deposits; accrued interest is calculated and verified (simple interest for the days in the year); posting moves principal onto the current or non-current Schedule III line and books the interest as other income. |
| 12 | T-20 Reconciliation engine | Bank, party or other reconciliation items. Timing differences need no entry; items marked "propose adjustment" become DRAFT journals that a different user approves on the Adjustments tab. |
| 12 | T-21 GSTR-9C | Books against GSTR-9C per GST head and table. Every non-zero difference is one issue in the tracker (source GSTR-9C) and clears itself at 0.00. |
| 13 | T-24 LFAR assistant | Loan schedule (limit, rate, security, EMI status) matched to ledgers by name, with the book balance read live from the adjusted TB (no second copy of any ledger), limit-overrun and overdue flags, and an eight-point LFAR checklist with live figures, status and remarks. Printable. |

### Step 14: closing the gaps against the brief
| Brief task | What was missing | What is there now |
|---|---|---|
| T-12 Issue Tracker | Grouping by cause, an "approved" status and an owner | **Group by cause** toggle with counts; **Approve** (Checker or Partner, note required, Maker gets 403) so an approved issue no longer blocks sign-off; an **owner** on every issue |
| T-18 UDIN dashboard | Signed reports without a UDIN were not "listed as exceptions" | Each signed version with no UDIN is one issue (`udin:v<n>`); recording the UDIN clears it, revoking re-opens it, and re-opening the engagement withdraws it |
| T-16 Audit Pack | No out-of-date flag | The pack records the data revision it was built or exported at, and shows a warning with a **Rebuild / Refresh Snapshot** button once anything changes |
| T-23 Materiality | Only year-on-year variances were flagged | GSTR-9C and reconciliation differences above overall materiality get an **ABOVE MATERIALITY** badge in the tracker and are listed on the Materiality screen |
| T-22 FD register | No dedicated FD note | A "Fixed deposits and accrued interest" schedule on the Notes screen, read from the FD register (posted FDs in the totals, drafts marked) |
| Comparatives | No way to enter prior-year figures | **Prior-year comparatives** panel on the Trial Balance tab: upload `Ledger Name, Prior Year Amount` or edit ledger by ledger. The statements, notes, checks and materiality update at once |

The navigation tabs are in audit-workflow order on a single row.

### Step 15: the open questions, answered and built
| Question | Decision implemented |
|---|---|
| Q1 sixth check | **CHK-06 Notes reconcile to statement face.** Every note total is recomputed from the adjusted trial balance, note by note, and compared with its line on the face of the Balance Sheet / P&L to the paisa. It passes on the balanced pilot, so all 6 of 6 checks pass and Partner sign-off works with no workaround. It fails, and blocks sign-off, if a note and its statement line differ. |
| Q2 entity template | Template Schedule III (Division I), Private Limited Company, is **confirmed** and the yellow banner is gone. An **Entity type** selector on the Trial Balance tab (Private Limited Company or Partnership Firm; Checker or Partner; locked at sign-off) relabels the equity lines to **Partners' Capital Account** and **Partners' Current Account** (and the notes and Word files follow). No figure changes. |
| Q4 output formats | PDF (browser Print / Save PDF) **and Word**. **Download Word (.docx)** buttons on Balance Sheet, Profit & Loss, Notes and Audit Pack produce A4 files with the company header, current and previous year in Indian currency format, the notes with the fixed-deposit schedule, the full audit pack, and Director / Partner and CA signature blocks (with the UDIN when recorded). |
| Q7 ratios | The nine ratios are labelled **Standard Schedule III / ICAI Analytical Ratios (Confirmed)**; the provisional banner is removed. Each ratio shows numerator, denominator, current year, previous year and **% variance**. |

Screens: Trial Balance & Import (with the entity type and prior-year comparatives), Mapping, Materiality, Adjustments, TDS / TCS, FD Register, Reconciliation, GSTR-9C, Balance Sheet, Profit & Loss, Notes, Ratios, Checks, Issues, Working Papers, LFAR, Audit Pack, Sign-Off, UDIN and Audit Trail.

## Local setup

Requirements: Windows PowerShell, Python 3.11+, Node.js, and a running PostgreSQL 16 server. PostgreSQL is **not** installed by this project.

1. Create the database `ca_workbench_fs` in PostgreSQL (once).
2. Copy `.env.example` to `.env` and set your password in `DATABASE_URL`:

```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ca_workbench_fs
```

3. Install, migrate and start. The first run creates `.venv`, installs the dependencies and applies migrations `001`-`015`:

```powershell
cd D:\FINANCIAL-STATEMENTS
.\run.ps1 -Init
```

The app is at <http://127.0.0.1:8015>. Later runs only need `.\run.ps1`.

Other commands:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_db --status   # list applied and pending migrations
.\.venv\Scripts\python.exe -m scripts.init_db            # apply pending migrations only
.\.venv\Scripts\python.exe -m scripts.make_samples       # regenerate the sample workbooks in samples\
.\.venv\Scripts\python.exe -m pytest -q                  # 91 tests
node tests/test_money.js                                 # 19 money-format checks
```

The tests create and drop their own `<db>_test` database. They do not touch your live data.

### Demo users
Pick the user from the switcher in the top bar. There are no passwords yet; real sign-in is a later step.

| User | Role | Can do |
|---|---|---|
| Maya Maker (Preparer) | MAKER | Import and map the TB, enter prior-year comparatives, draft adjustments, assign issue owners, raise and clear issues, attach working papers, enter TDS/TCS, FD, reconciliation, GSTR-9C and LFAR data, propose reconciliation adjustments, export the pack |
| Chetan Checker (Reviewer) | CHECKER | Approve adjustments and issues, review working papers, run checks, set materiality, post the TDS/TCS and FD registers, plus the Maker's data entry |
| Priya Partner | PARTNER | Everything above, plus sign off, reopen for revision, and record or revoke UDINs |

### Test files
Sample data lives in `sample_data\` and `samples\`. Upload them on the matching screen.

| File | Screen | What it contains |
|---|---|---|
| `sample_data/9and9_pilot_tb_76_ledgers.csv` | Trial Balance & Import | The pilot trial balance: 76 ledgers |
| `samples/tds_tcs_pack.xlsx` | TDS / TCS | 8 rows: 194C, 194J, 194I, 194A, TDS receivable and TCS payable |
| `samples/fd_register.xlsx` | FD Register | 5 bank FDs (HDFC, SBI, ICICI), current and non-current, with FY 2026-27 accrued interest |
| `samples/bank_reconciliation.xlsx` | Reconciliation | 6 items: 2 timing differences and 4 unbooked bank items |
| `samples/gstr9c_reconciliation.xlsx` | GSTR-9C | 4 rows: CGST and SGST matched, IGST and CESS with differences |
| `samples/loan_schedule.xlsx` | LFAR | 4 loan facilities mapped to the pilot's borrowing ledgers; one is over its limit and overdue |

The files are generated by `scripts/make_samples.py` from ledger names that exist in the pilot trial balance.

## Product Owner decisions: all resolved

| # | Question | Resolution |
|---|---|---|
| Q1 | The sixth check | **Resolved / Implemented (Step 15).** CHK-06 Notes reconcile to statement face. |
| Q2 | Entity type and template | **Resolved / Implemented (Step 15).** Schedule III (Division I) for a Private Limited Company is confirmed; a Partnership Firm option relabels the equity lines. |
| Q3 | Tally report and fields | **Resolved.** The Tally trial balance (ledger, group, opening, debit, credit, closing) in CSV or Excel, as in `sample_data`. |
| Q4 | Output formats | **Resolved / Implemented (Step 15).** PDF through the browser, and Word (.docx). |
| Q5 | Which WP9 modules | **Resolved (Steps 10-13).** All six: materiality, TDS/TCS, FD register, reconciliation, GSTR-9C, LFAR. |
| Q6 | Materiality input | **Resolved (Step 10).** Both manual and calculated (% of a benchmark). |
| Q7 | Ratio definitions | **Resolved / Implemented (Step 15).** The nine standard Schedule III / ICAI analytical ratios, confirmed, with the previous year and % variance. |

CHK-03 (notes tie to statement lines) and CHK-06 both compare notes with the statements; CHK-06 recomputes the note totals independently, straight from the adjusted trial balance, so the two will normally pass or fail together.

## Known limitations
- **Prior-year comparatives** are 0.00 until you enter them (Trial Balance tab). Until then the comparative columns are empty and materiality flags no year-on-year variance.
- Posting the TDS/TCS and FD registers **adds** to the trial balance, so post only items that are not already booked.
- The browser's print preview, PDF output and the Word files have been checked by tests (valid files, correct text and figures) but not opened in Word or looked at on paper.
- The locked, reopened and v2 sign-off screens, the Word download buttons, and the upload-and-post flows of Steps 11-14 are covered by API tests but have not all been clicked through in a browser.
- Reversals of reversals build long nested descriptions (cosmetic).
- The FD note lists the register's FDs; the statements themselves show FDs on their Schedule III lines (and interest in Other income), not as a separate statement line.
- Sign-in uses the `X-User-Id` dev switcher, not real authentication.
