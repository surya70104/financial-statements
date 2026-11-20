# CA Workbench: Financial Statements and Connected Modules

A local web app for Chartered Accountants to turn a Tally trial balance into Schedule III financial statements, with adjustments, checks, working papers, an Audit Committee pack, versioned Partner sign-off, and the connected modules (materiality, TDS/TCS, fixed deposits, reconciliation, GSTR-9C and LFAR).

Pilot client: **9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED**, FY 2026-27, Statutory Audit. The spec is `Financial_Statements_Developer_Task_Brief.docx` (not in this repo).

**Status:** all three passes are built and ready for review: Pass 1 (Steps 1-5), Pass 2 (Steps 6-9) and Pass 3 (Steps 10-13, the WP9 connected modules). The product questions Q1-Q7 below are still open and affect labels, one check and the ratio formulas, not the build.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Database | PostgreSQL 16, `psycopg` 3 with plain SQL (no ORM) |
| Frontend | Server-rendered Jinja2 pages with vanilla JavaScript (no framework, no build step) |
| Money | `NUMERIC(18,2)` rupees in the database and exact string decimals in the API. The shared `money.js` handles display (Lakhs or Rupees, Indian grouping, negatives in brackets) and exact paise comparisons. |
| Files | `openpyxl` reads `.xlsx` uploads; `.csv` is also accepted |
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

Screens: Trial Balance & Import, Mapping, Adjustments, Checks, Balance Sheet, Profit & Loss, Working Papers, Issues, TDS / TCS, FD Register, Reconciliation, GSTR-9C, LFAR, Materiality, Sign-Off, UDIN, Audit Pack, Audit Trail, Notes and Ratios.

## Local setup

Requirements: Windows PowerShell, Python 3.11+, Node.js, and a running PostgreSQL 16 server. PostgreSQL is **not** installed by this project.

1. Create the database `ca_workbench_fs` in PostgreSQL (once).
2. Copy `.env.example` to `.env` and set your password in `DATABASE_URL`:

```
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ca_workbench_fs
```

3. Install, migrate and start. The first run creates `.venv`, installs the dependencies and applies migrations `001`-`013`:

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
.\.venv\Scripts\python.exe -m pytest -q                  # 80 tests
node tests/test_money.js                                 # 19 money-format checks
```

The tests create and drop their own `<db>_test` database. They do not touch your live data.

### Demo users
Pick the user from the switcher in the top bar. There are no passwords yet; real sign-in is a later step.

| User | Role | Can do |
|---|---|---|
| Maya Maker (Preparer) | MAKER | Import and map the TB, draft adjustments, raise and clear issues, attach working papers, enter TDS/TCS, FD, reconciliation, GSTR-9C and LFAR data, propose reconciliation adjustments, export the pack |
| Chetan Checker (Reviewer) | CHECKER | Approve adjustments, review working papers, run checks, set materiality, post the TDS/TCS and FD registers, plus the Maker's data entry |
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

## Pending Product Owner decisions

The build is complete for review. These questions from the brief are still open; the app works in the meantime using the defaults noted.

| # | Question | What it unblocks | How the app behaves now |
|---|---|---|---|
| Q1 | Name the sixth check and write its rule | CHK-06 passes for real | CHK-06 is "pending" and fails, so it blocks every genuine sign-off. The tests use a stand-in rule. |
| Q2 | Is the entity a company (share capital, reserves and surplus) or a partnership? Is template AM-STAT correct? | Correct equity labels and mapping; the FD lines | A banner says the template is not confirmed. The FD register assumes current FDs on Cash and cash equivalents and non-current on Other non-current assets. |
| Q3 | Which Tally report and fields are imported? | Confirms the TB import format | The Tally trial balance CSV or Excel format in `sample_data` is used. |
| Q4 | Output formats for Schedule III and the IA Report: PDF, Word or both | Output work | Browser Print / Save PDF only. Word output would be new work. |
| Q5 | Which WP9 modules are in this release? | Scope of WP9 | All six are built (T-19 to T-24). |
| Q6 | Is materiality entered manually or calculated? | The T-23 input | Both are supported. |
| Q7 | Which ratio definitions? | Formulas on the Ratios tab | Provisional formulas, shown beside each ratio. |

## Known limitations
- There is no screen to enter **prior-year comparatives**; they are seeded as 0.00. Materiality therefore flags nothing on the pilot until comparatives exist (the tests set them in the database).
- Posting the TDS/TCS and FD registers **adds** to the trial balance, so post only items that are not already booked.
- The browser's print preview and PDF output have not been checked by eye.
- The locked, reopened and v2 sign-off screens, and the upload-and-post flows of Steps 11-13, are covered by API tests but have not all been seen in the browser.
- Reversals of reversals build long nested descriptions (cosmetic).
- Sign-in uses the `X-User-Id` dev switcher, not real authentication.
