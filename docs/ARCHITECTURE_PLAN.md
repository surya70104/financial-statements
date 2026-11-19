# CA Workbench: Financial Statements & Connected Modules
## Technical Architecture & Build Plan (for review, no application code yet)

Source: `Financial_Statements_Developer_Task_Brief.docx` (Draft 1, 18 Nov 2026). Pilot: 9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED, FY 2026-27, Statutory Audit, Private Limited.
Companion file: [schema_draft.sql](schema_draft.sql) holds the full PostgreSQL DDL (tables, keys, constraints, indexes, triggers, views). **The DDL has not been executed**: PostgreSQL is not installed on this machine, so syntax is reviewed by eye only. First action after approval is to run it on a local PostgreSQL 16.

---

## 0. Brief inconsistencies I found, and how this plan resolves them

| # | Finding in the brief | Resolution in this plan |
|---|---|---|
| A1 | Build step 2 (Statements) needs the adjusted TB (T-06), but WP2 (which contains T-06) is build step 5. | The adjusted TB is a **database view** (`v_adjusted_tb`) created in Step 1. Before Step 5 it simply has zero approved adjustments. Step 5 adds only the entry/approval screens. |
| A2 | T-11 lists T-09 as a dependency, but the notes check is added "after step 4". | Check CHK-03 exists from Step 3 with `enabled_from_step = 4`; it shows "not yet active" until then. |
| A3 | Section 3 says statements "lock when all checks pass" (gate, T-13) and also "lock on sign-off" (T-17). Outputs (Step 8) need "approved statements" before sign-off (Step 9). | Version states: `draft` → `approved` (gate cleared, figures frozen in a snapshot; outputs may be built) → `signed_off` (locked). Any later data change supersedes the version and withdraws sign-off. **Please confirm.** |
| A4 | Baseline says "5 of 6 passing" but never says which check fails, and Q1 says the 6th check is unknown. | CHK-06 is a placeholder row `pending_definition`. It counts in the "x of 6" total, is never shown as passing, and blocks sign-off until Q1 is answered. |
| A5 | T-03 says exception count equals unmapped-ledger count, yet the baseline has 5,433 open exceptions for 76 ledgers. 5,433 is not a multiple of 76 (76 × 71.5), so it is not simply "whole file re-imported N times". | See section 5. The new design makes the count mathematically bounded (unique key) and testable. |
| A6 | T-08: "profit equals movement in reserves" ignores dividends, transfers, or prior-period items. | Check CHK-02 compares profit to reserve movement **after** an explicit, auditable "other movements in reserves" input (default 0). Needs a CA decision. |

---

## 1. Technology stack (Windows PowerShell, local)

| Concern | Choice | Why |
|---|---|---|
| Runtime | Python 3.11 (already installed here), FastAPI + Uvicorn | per brief |
| DB | PostgreSQL 16, driver `psycopg` 3 | `NUMERIC`, partial indexes, deferred constraint triggers |
| ORM / migrations | SQLAlchemy 2.0 (Core + ORM), Alembic | Views/triggers go in migrations as raw SQL |
| Validation | Pydantic v2 | Money serialised as **strings**, parsed as `Decimal` |
| Templates | Jinja2 server-rendered shells + Vanilla JS (ES modules, no build step) | per brief; no npm needed |
| Auth | Local users table, bcrypt (`passlib`), signed session cookie, RBAC from `role_permissions` | "authorised user" for clearance, maker-checker for approvals |
| Import parsing | `csv` / `openpyxl` (xlsx) / `lxml` (xml, only if Q3 says so) | |
| Tests | pytest + a throw-away PostgreSQL schema per test session | triggers/constraints need a real DB, not SQLite |
| PDF/Word (Q4) | Decided at Step 8. Default: print-CSS HTML (browser "Save as PDF") because WeasyPrint is painful on Windows; `python-docx` if Word is required | isolated behind a renderer interface |

Local run (will be scripted in `scripts/`):

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
createdb caworkbench; alembic upgrade head; python -m scripts.seed
uvicorn app.main:app --reload --port 8000
```

---

## 2. Project folder & file structure

```
D:\FINANCIAL-STATEMENTS\
├─ docs\                         ARCHITECTURE_PLAN.md, schema_draft.sql, decisions\ (Q1-Q7 answers as ADRs)
├─ alembic\ versions\            0001_core.py, 0002_views_triggers.py, 0003_seed_templates.py ...
├─ app\
│  ├─ main.py                    app factory, middleware, router mounting
│  ├─ config.py                  settings (DB URL, secrets) from .env
│  ├─ db\
│  │  ├─ session.py              engine, session; sets  SET LOCAL app.user_id / app.request_id per transaction
│  │  └─ models\                 SQLAlchemy models, one file per domain
│  │     ├─ identity.py          users, roles, permissions
│  │     ├─ templates.py         statement_templates, template_lines/notes, crosswalk, ratio_definitions
│  │     ├─ engagement.py        clients, engagements, statement_versions, snapshots
│  │     ├─ tally.py             import_profiles, tally_groups, ledgers, ledger_balances, upload_history, tb_import_rows
│  │     ├─ mapping.py           ledger_mappings
│  │     ├─ journal.py           journal_entries, journal_lines
│  │     ├─ checks.py            check_definitions, check_runs, check_results
│  │     ├─ exceptions.py        exception_causes, exceptions
│  │     ├─ evidence.py          working_papers, working_paper_links
│  │     ├─ outputs.py           output_documents, report_signoffs, udin_records
│  │     └─ audit.py             audit_logs (read-only model)
│  ├─ core\
│  │  ├─ money.py                Decimal helpers, quantize, parse; the ONLY place money is rounded
│  │  ├─ formatting.py           Indian grouping, lakh/rupee, bracket negatives (server side, used by outputs)
│  │  ├─ security.py             auth, permission dependency  require("exception.clear")
│  │  └─ errors.py               DomainError → HTTP mapping
│  ├─ services\                  ALL business logic; routers stay thin
│  │  ├─ change_coordinator.py   after any statement-affecting change: bump revision → supersede version → re-run checks → sync exceptions → audit EVENT (one transaction)
│  │  ├─ engagement_service.py
│  │  ├─ importing\
│  │  │  ├─ base.py              TrialBalanceReader interface → normalised TBRow list
│  │  │  ├─ tally_csv.py, tally_xlsx.py   (Q3 adapters, selected by import_profiles)
│  │  │  └─ upsert.py            idempotent ledger/balance upsert + deactivation of absent ledgers
│  │  ├─ mapping_service.py
│  │  ├─ journal_service.py      draft → approve → reverse
│  │  ├─ adjusted_tb.py          thin reader over v_adjusted_tb
│  │  ├─ statements\
│  │  │  ├─ engine.py            evaluates template formulas (subtotal/derived) on Decimal
│  │  │  ├─ balance_sheet.py, profit_loss.py, notes.py
│  │  │  └─ ratios.py            safe expression evaluator over template line codes (Q7)
│  │  ├─ checks\
│  │  │  ├─ registry.py          rule_type → function; one file per rule
│  │  │  ├─ balance_check.py, profit_reserves.py, notes_tie.py, mapping_complete.py, comparatives.py
│  │  │  └─ chk06_placeholder.py (Q1)
│  │  ├─ exceptions_service.py   sync_exceptions() (the ONLY writer of automated exceptions), clear, approve, group
│  │  ├─ signoff_gate.py         reads v_signoff_blockers
│  │  ├─ working_papers.py
│  │  ├─ versioning.py           version create/supersede/lock, content hashing
│  │  ├─ outputs\                renderers (html_print.py, docx.py, pdf.py) + pack builder (Q4)
│  │  ├─ signoff_service.py, udin_service.py
│  │  └─ audit_service.py        EVENT writer; DB triggers write row-level audit
│  ├─ routers\                   one file per build step (see section 4): step1_*.py ... step9_*.py, ui.py
│  ├─ schemas\                   Pydantic request/response models (money = str)
│  ├─ templates\                 Jinja2: base.html, partials\ (engagement_header, unit_toggle, checks_badge), step1\ ... step9\
│  └─ static\
│     ├─ css\                    app.css (tokens, tables, print.css for outputs)
│     └─ js\
│        ├─ core\                money.js (formatter + unit state), api.js (fetch wrapper, error toasts), table.js, dom.js
│        └─ pages\               mapping.js, statements.js, checks.js, adjustments.js, issues.js, wp.js, outputs.js, signoff.js
├─ scripts\                      seed.py, run_dev.ps1, diagnose_pilot.py (section 5), load_pilot_tb.py
├─ tests\
│  ├─ unit\                      money/formatting vectors, ratio evaluator, check rules
│  ├─ db\                        constraint & trigger tests (unbalanced entry, immutability, audit append-only, dedupe)
│  ├─ integration\               per build step, run against the pilot TB fixture
│  └─ fixtures\pilot\            pilot trial balance (76 ledgers) + approved values from brief section 7
├─ requirements.txt, alembic.ini, .env.example, README.md
```

Layering rule: `routers → services → models/db`. Routers never touch SQL; services never import FastAPI. Rules that must hold even if someone bypasses the service layer live in the database (constraints/triggers), not only in Python.

---

## 3. PostgreSQL schema (summary; full DDL in `schema_draft.sql`)

### 3.1 Design principles
1. **Money** `NUMERIC(18,2)` rupees, **signed** (Dr +, Cr −) in the ledger tables; presentation sign comes from the template. No `float` anywhere. API sends amounts as strings.
2. **No stored statement figures.** Balance Sheet, P&L, notes, and ratios are computed on read from `v_adjusted_tb`. The only stored figures are the frozen `statement_snapshots` written by the system at approval (they exist to prove "what was signed").
3. **Everything that must never be violated is a constraint or trigger**, not a convention.

### 3.2 Tables by area

| Area | Tables | Key constraints / indexes |
|---|---|---|
| Identity | `users`, `roles`, `user_roles`, `role_permissions` | unique `lower(email)`; user 1 = `system` |
| Templates (Q2, Q7) | `statement_templates`, `template_lines`, `template_notes`, `template_line_crosswalk`, `ratio_definitions` | `UNIQUE(template_id, code)`; `UNIQUE(id, line_id)` on notes so a mapping's note must belong to its line |
| Engagement & versions | `clients`, `engagements`, `statement_versions`, `statement_snapshots` | `UNIQUE(client_id, fy_label, engagement_type)`; `UNIQUE(engagement_id, version_no)`; partial unique index → **one live version per engagement**; `data_revision` counter on engagement |
| Import (T-01, Q3) | `import_profiles`, `tally_groups`, `ledgers`, `ledger_balances`, `upload_history`, `tb_import_rows` | `UNIQUE(engagement_id, ledger_key)` is the idempotency key; `ledger_balances` has **one row per ledger** (PK = `ledger_id`) and `CHECK closing = opening + Dr − Cr` |
| Mapping (T-02/03) | `ledger_mappings` | `UNIQUE(ledger_id)` ⇒ one line + one note per ledger; composite FK `(note_id,line_id)`; trigger forces line to belong to the engagement's template; keyed to the persistent `ledger_id`, so mappings survive re-import |
| Adjustments (T-04/05) | `journal_entries`, `journal_lines` | deferred constraint trigger: Σdebit = Σcredit **at COMMIT**; `CHECK (debit=0) <> (credit=0)`; triggers block UPDATE/DELETE of approved entries/lines; `UNIQUE(reverses_entry_id)` allows one reversal per entry; `CHECK` approved ⇒ approver + time |
| Checks (T-11, Q1) | `check_definitions`, `check_runs`, `check_results` | `variance` is a **generated column** `lhs − rhs` (exact, to the paisa); `CHECK` pass ⇒ zero variance |
| Issue tracker (T-12/13) | `exception_causes`, `exceptions` | **`UNIQUE(engagement_id, source, reference_id)`**; `CHECK` cleared ⇒ reason + actor; `occurrences` counter; partial index on open |
| Evidence (T-14) | `working_papers`, `working_paper_links` | `CHECK` link targets exactly one of line/note; partial unique indexes prevent duplicate links |
| Outputs & sign-off (T-15..18) | `output_documents`, `report_signoffs`, `udin_records` | one active sign-off per (version, report type); UDIN `^[A-Z0-9]{18}$` and globally unique |
| Audit | `audit_logs` | append-only: row triggers block UPDATE/DELETE, statement trigger blocks TRUNCATE, app DB role has no such privileges |

### 3.3 Views (single source of truth)
- `v_adjusted_tb`: ledger-by-ledger `tb_balance + Σ approved journal lines` with mapped line/note. **Statements read only this view (T-06).**
- `v_unmapped_ledgers`: one predicate used both by the Mapping screen count and by exception sync, so the two counts cannot disagree (T-03).
- `v_line_totals`, `v_note_totals`: aggregated by line and note (note totals use the same view, same sign rule, so the notes-tie check compares like with like).
- `v_latest_check_results`, `v_exception_summary`, `v_signoff_blockers`, `v_unlinked_lines_notes`, `v_signed_without_udin`: each backs exactly one screen/count.

### 3.4 Requirement-to-mechanism map

| Requirement | Mechanism |
|---|---|
| Re-import never duplicates 76 ledgers | `INSERT … ON CONFLICT (engagement_id, ledger_key) DO UPDATE`; `ledger_balances` upsert on PK; ledgers missing from a new file are set `is_active=false` (never deleted, so mapping persists) and raise a "ledger dropped" exception |
| Reproduce Tally closing for every ledger | closing stored from file in `closing_per_source`; CHECK `closing = opening + Dr − Cr`; any mismatch recorded in `upload_history.closing_mismatch_count` and blocks commit |
| Every import logged | `upload_history` row written even on failure (`status='failed'` + `error_detail`), plus file SHA-256 to flag identical re-uploads |
| Dr = Cr on save | deferred constraint trigger (DB) + Pydantic validation (friendly message) |
| Approved entries immutable | `journal_entries_guard` / `journal_lines_guard`; correction = new entry with `reverses_entry_id` (auto-swapped Dr/Cr, goes through approval) |
| Only approved adjustments reach statements | `v_adjusted_tb` filters `status='approved'` |
| Creator / approver recorded | NOT NULL `created_by`, `created_at`; CHECK for approver |
| Exact rupee variance per check | `check_results.variance` generated column |
| No duplicate exceptions | unique key + `sync_exceptions()` (section 5) |
| Role-gated clearance with reason | permission `exception.clear` checked in service; DB CHECK requires reason + actor |
| Sign-off blocker gate | `v_signoff_blockers` lists every failing check and every open exception; gate = no rows |
| Sign-off auto-revoked on change | `ChangeCoordinator` (below) |
| Immutable audit trail | `audit_row_change()` trigger on every business table + EVENT rows for recalculation/sign-off events; refuses to run if `app.user_id` is unset |

### 3.5 Versioning and auto-revocation flow (T-17, Shared Rule 5)
`engagements.data_revision` is bumped inside **`ChangeCoordinator.after_change()`**, called by every service method that changes mapping, import, or approves/reverses an adjustment (draft adjustments do not affect statements, so they do not bump). In the same transaction it:
1. increments `data_revision`;
2. if the live version is `approved` or `signed_off`: sets it `superseded`, creates version N+1 `draft` with `created_reason` (e.g. "mapping change: ledger Sales-Domestic"), and flips active `report_signoffs` to `withdrawn` with the reason;
3. re-runs all enabled checks → new `check_runs` row;
4. runs `sync_exceptions()` for sources `mapping` and `check`;
5. writes an `EVENT` row to `audit_logs` (`RECALCULATED`, `SIGNOFF_WITHDRAWN`).

Because statements are computed live, "recalculate everything downstream" means steps 3–4 only; there is nothing stale to refresh. Out-of-date packs (T-16) are detected by comparing `output_documents.built_from_hash` with the hash of the current live statement.

---

## 4. Backend API & frontend screen blueprint, in Build Step order

Base path `/api/v1`. Shared page shell on every screen: **engagement header** (client, FY, status, template + "unconfirmed" banner if Q2 open, materiality and who set it, version no. + status, checks badge "x of 6", open-exceptions count) and the **unit toggle** (Lakh ⇄ Rupees).

### Step 0 (inside Step 1): platform foundation
Login, session, RBAC; DB session context for audit; `ChangeCoordinator`; `sync_exceptions` and the `exceptions`/`audit_logs` tables (needed because T-03 writes exceptions in Step 1, even though the full Issue Tracker UI is Step 6); money/format modules; engagement header partial; seed of templates, causes, check definitions, roles.

### Step 1: WP1 Trial balance import & mapping (T-01, T-02, T-03)
| Route | Purpose |
|---|---|
| `POST /engagements`, `GET /engagements/{id}`, `GET /engagements/{id}/header` | create pilot engagement; header payload |
| `POST /engagements/{id}/template/confirm` | Q2 confirmation (user + time) |
| `GET /import-profiles` | Q3 profiles |
| `POST /engagements/{id}/imports?dry_run=true\|false` | multipart upload; dry run returns diff (new / changed / dropped ledgers, closing mismatches) before commit |
| `GET /engagements/{id}/imports`, `GET /imports/{id}` | Upload history |
| `GET /engagements/{id}/trial-balance` | ledgers + balances (filter mapped/unmapped) |
| `GET /templates/{id}/lines`, `GET /templates/{id}/notes` | mapping dropdown data |
| `GET /engagements/{id}/mapping` · `PUT /ledgers/{id}/mapping` · `POST /engagements/{id}/mapping/bulk` · `DELETE /ledgers/{id}/mapping` | map one/many; each triggers `ChangeCoordinator` |
| `GET /engagements/{id}/mapping/unmapped` | unmapped list; count equals exception count (T-03) |
| `GET /engagements/{id}/audit-logs` | read-only audit viewer (useful from day one) |

Screens: **Engagement dashboard**, **Import** (drop file → preview diff → commit), **Upload history**, **Mapping** (grid: ledger, Tally group, balance, line dropdown, note dropdown, status chip; filter "unmapped"; bulk-assign by group; unmapped count header), **Audit log**.

### Step 2: T-07 Balance Sheet, T-08 P&L
`GET /engagements/{id}/statements/balance-sheet`, `/profit-loss` (`?comparative=true`). Response is a tree of `{code,label,amount,py_amount}` with amounts as exact strings. Screens: **Balance Sheet**, **Profit & Loss** (Schedule III layout, comparatives column, unit toggle, drill-down from a line to its ledgers).

### Step 3: WP4 Checks engine (T-11)
`GET /engagements/{id}/checks` (latest run; count + per-check lhs/rhs/variance), `POST /engagements/{id}/checks/run`, `GET /engagements/{id}/checks/history`. Screen: **Checks** tab ("5 of 6 passing", each failing check named with its exact variance; CHK-06 "awaiting definition (Q1)").

### Step 4: T-09 Notes, T-10 Ratios
`GET /engagements/{id}/notes`, `/notes/{note_no}` (ledger-level breakdown), `GET /engagements/{id}/ratios`. Enables CHK-03. Screens: **Notes** (each note with tie-to-line indicator), **Ratios** (value + formula beside it + "provisional" badge until Q7).

### Step 5: WP2 Adjustments (T-04, T-05, T-06 UI)
`GET/POST /engagements/{id}/adjustments`, `GET/PUT/DELETE /adjustments/{id}` (draft only), `POST /adjustments/{id}/approve`, `POST /adjustments/{id}/reverse`, `GET /engagements/{id}/adjusted-tb`, `GET /adjustments/{id}/impact` (what-if effect on BS/PL/checks before approving). Screens: **Adjustments list**, **Adjustment form** (Dr/Cr lines, live "Dr − Cr = 0" indicator, save disabled until balanced), **Adjusted TB** (TB | adjustments | adjusted, per ledger).

### Step 6: WP5 Issue Tracker & gate (T-12, T-13; T-23 flag)
`GET /engagements/{id}/exceptions?status&source&cause&owner`, `GET …/exceptions/grouped` (by cause), `PATCH /exceptions/{id}` (owner), `POST /exceptions/{id}/clear`, `POST /exceptions/{id}/approve`, `POST /engagements/{id}/exceptions` (manual), `GET /engagements/{id}/signoff-gate`, `PUT /engagements/{id}/materiality`. Screens: **Issue Tracker** (list, group-by-cause toggle, clear/approve dialogs requiring a reason, role-aware buttons), **Sign-off gate panel** (lists each blocker with deep link).

### Step 7: WP6 Working paper links (T-14)
`GET/POST /engagements/{id}/working-papers`, `POST/DELETE /working-papers/{id}/links`, `GET /engagements/{id}/working-papers/unlinked`. Screens: **Working papers** (register), **Link matrix** (lines/notes × papers, "No working paper" filter).

### Step 8: WP7 Outputs (T-15, T-16)
`POST /engagements/{id}/approve-statements` (runs the gate; on success freezes snapshot, version → `approved`), `POST /engagements/{id}/outputs/{kind}` (SCHEDULE_III | IA_REPORT | AC_PACK), `GET /engagements/{id}/outputs` (each flagged `out_of_date`), `GET /outputs/{id}/download`. Screens: **Outputs** (build, download, statement date + version shown, red "out of date" flag), print-ready view with `print.css`. A line-by-line match test compares output to on-screen statements.

### Step 9: WP8 Sign-off & UDIN (T-17, T-18)
`GET /engagements/{id}/versions`, `POST /engagements/{id}/signoff`, `GET /engagements/{id}/signoffs`, `POST /signoffs/{id}/udin`, `GET /udin-dashboard`. Screens: **Report & Sign-off** (blocker list or sign button; version history), **UDIN Dashboard** (signed reports, UDIN, "missing UDIN" exceptions).

### Later: WP9 (hooks only now, built when Q5 is answered)
`journal_entries.source` already allows `reconciliation | tds | fd`, so T-19/T-20/T-22 feed the same draft→approved path; T-21 uses `exceptions(source='reconciliation')`; T-24 LFAR gets a read-only DB role on `v_adjusted_tb` (no second ledger copy); T-23 materiality columns exist from Step 1.

### 4.1 Shared Rule 1: money display
- **Storage**: `NUMERIC(18,2)`; Python `Decimal`; JSON strings (`"272641256.00"`). Rounding only happens in `core/money.py` (server) and `money.js` (client), half-up, and **only at display time**.
- **Formatting algorithm** (identical in `core/formatting.py` and `static/js/core/money.js`, both pure string/BigInt arithmetic with no floats):
  1. Lakh mode shifts the decimal point 5 places (÷100,000) and shows 2 decimals; Rupee mode shows 0 decimals, half-up.
  2. Indian grouping: last 3 digits, then pairs (`27,26,41,256`).
  3. Negatives in brackets with symbol outside: `₹(4,81,634)`; in lakh `₹(4.82)`.
  4. Zero shows `₹0` / `₹0.00`; "unit label" (`₹ in lakh`) is rendered in the column header, not each cell.
- **Toggle**: header control sets `unit` in `localStorage` (fallback `engagements.default_unit`) and re-renders from the already-loaded exact strings, with no refetch and no rounding drift.
- **Checks are exact**: all comparisons use `NUMERIC` paise, never the displayed rounded value. A variance below ₹1 is shown with paise so a "rounding" difference cannot hide.
- **Test vectors** shared by pytest and a browser test page: `272641256 → 27,26,41,256`, `-481634 → ₹(4,81,634)`, `-481634 (lakh) → ₹(4.82)`, `0`, `0.50`, `-0.50`, `99999.995`, `100000` and `1e9`-scale values.

### 4.2 Other Shared Rules
| Rule | Enforcement |
|---|---|
| No typed statement figure | No table or endpoint accepts a statement amount; snapshots are written only by the system; a test greps routers for any amount input outside journal lines and the TB importer |
| Change ⇒ recalculation + audit entry | `ChangeCoordinator` is the only path for statement-affecting writes; DB triggers log the rows; EVENT row logs "recalculated" with user and time |
| Locked after sign-off | A change after sign-off never edits the signed version; it supersedes it and opens a new draft |

---

## 5. Pilot baseline anomalies

### 5.1 How the 5,433-duplicate-exception bug is prevented
Hypothesis (to be confirmed against the old data, which I cannot see): the legacy tool **inserts** an exception on every import, mapping save, or check run instead of upserting by identity, so counts grow with each run and with each ledger × check pairing. The numbers are consistent with that, not with a single repeated import (5,433 ÷ 76 is not an integer).

Prevention, in layers:
1. **Identity**: every exception has a deterministic `reference_id` (`LEDGER:<ledger_key>`, `CHECK:CHK-03:NOTE:7`, `GSTR9C:2026-27:<ledger_key>`), and `UNIQUE(engagement_id, source, reference_id)`.
2. **One writer**: only `sync_exceptions(engagement, source, active_set)` creates automated exceptions. It receives the *complete current set* of conditions and does:
   ```sql
   INSERT INTO exceptions (...) VALUES (...)
   ON CONFLICT (engagement_id, source, reference_id) DO UPDATE
     SET last_seen_at = now(), occurrences = exceptions.occurrences + 1, amount = EXCLUDED.amount,
         is_condition_active = TRUE,
         status = CASE WHEN exceptions.status <> 'open' AND exceptions.fingerprint <> EXCLUDED.fingerprint
                       THEN 'open' ELSE exceptions.status END,   -- a cleared item re-opens only if the facts changed
         fingerprint = EXCLUDED.fingerprint;
   ```
   then auto-closes (`cleared_by_system`) items of that source no longer in the active set.
3. **Ledger identity is stable** (`ledger_key`), so a re-import cannot create "new" ledgers that look unmapped.
4. **Count invariants as tests**: `open mapping exceptions == COUNT(v_unmapped_ledgers)` (T-03); `Σ grouped-by-cause == open count` (T-12); `open mapping exceptions ≤ ledger count` always; importing the pilot file 5× leaves 76 ledgers and an unchanged exception count; and the negative test from brief section 8.
5. Both header count and group-by-cause read `v_exception_summary`, so they cannot drift.

### 5.2 Diagnosing the ₹0 revenue anomaly
The balance sheet balances and a loss of ₹(4,81,634) is shown while revenue is ₹0. If unmapped income ledgers were simply excluded, the BS would not balance, so my leading hypothesis is that the legacy **profit is a balancing figure** (plug) rather than the sum of P&L lines, which hides unmapped or wrongly mapped income. Other candidates: income mapped to a non-revenue line (e.g. "Other income" or a liability), a sign error on credit-natured balances, or the P&L aggregator selecting the wrong line code.

Plan: ship `scripts/diagnose_pilot.py` (read-only) after the pilot TB is loaded, reporting:
1. every ledger whose Tally group nature is `INCOME`, with balance and its mapped line/note (or "unmapped");
2. sum of `INCOME` / `EXPENSE` ledgers by Tally nature versus by Schedule III line (the two must agree);
3. ledgers mapped to a line whose section contradicts the Tally group nature;
4. sign check: income ledgers should be credit (negative in our convention);
5. the "revenue from operations" line total and the ledgers composing it.

New safeguards so this cannot hide in the new build: (a) profit is **computed from P&L lines**, never as a plug; (b) a diagnostic check compares P&L-line profit to Tally-nature profit (Σ INCOME + EXPENSE ledgers) and raises an exception on any difference; (c) a "nature vs section" mapping warning in the Mapping screen. This is shown as a diagnostic, not one of the six counted checks, until Q1 decides.

---

## 6. Open questions Q1-Q7: isolation so Step 1 is never blocked

| Q | What's unknown | Where it is isolated | Dev default until answered | What changes when answered |
|---|---|---|---|---|
| Q1 | 6th check | `check_definitions` row `CHK-06` (`pending_definition`) + `checks/chk06_placeholder.py`; `total_count` fixed at 6 | Shows "awaiting definition", counted as not passing, blocks sign-off | Write one rule file, set `rule_type`/`rule_config`, status `confirmed`. Candidates for the PO: TB debits = credits; closing = opening + movement; adjustments balanced; P&L-vs-Tally-nature profit tie (section 5.2) |
| Q2 | Equity labels / AM-STAT vs company | `statement_templates` + `template_lines.label`; `engagements.template_id`; confirmation fields | Seed both `SCH3-COMPANY` (share capital, reserves & surplus) and `AM-STAT` (partners' funds); pilot set to company with an "unconfirmed" banner | Confirm in UI. A template switch uses `template_line_crosswalk` to re-point mappings automatically; labels are data, no code change |
| Q3 | Tally report/fields | `import_profiles.column_map` + reader adapters behind `TrialBalanceReader` | Generic CSV/XLSX profile: ledger, group, opening, debit, credit, closing (+ optional GUID) | New profile row (or adapter if XML). Downstream tables never see file formats |
| Q4 | PDF / Word | Renderer interface in `services/outputs/`; `output_documents.format` | Print-CSS HTML | Add `pdf.py` or `docx.py`; Step 8 only |
| Q5 | Which WP9 modules | `journal_entries.source`, `exceptions.source`, read-only views | Hooks only, nothing built | Schedule per module |
| Q6 | Materiality manual vs calculated | `engagements.materiality_*` incl. `method` and `set_by` | Manual entry | Add calculated-basis helper; schema unchanged |
| Q7 | Ratio formulas | `ratio_definitions.formula` evaluated by a safe parser over line codes; `status='provisional'` | Standard set (current, quick, debt-equity, net-profit %, etc.) badged "provisional" | Edit rows, flip to `confirmed`; no deploy |

---

## 7. Test strategy mapped to brief section 8
- **Golden pilot fixture**: pilot TB (76 ledgers) loaded; asserts total assets ₹27,26,41,256 = equity + liabilities, profit ₹(4,81,634), checks count, revenue as diagnosed.
- **Per-task acceptance tests** named by task ID (`test_T01_reimport_is_idempotent`, `test_T04_unbalanced_entry_refused`, …).
- **Regression test**: change one mapping, assert BS, P&L, notes, checks, and exceptions all change, and an audit row exists.
- **Negative test**: unmap one ledger, assert exactly one exception and sign-off blocked with that reason.
- **DB-level tests**: approved entry UPDATE/DELETE fails; unbalanced commit fails; `audit_logs` UPDATE/DELETE/TRUNCATE fail; missing `app.user_id` fails; duplicate exception insert hits the unique key.
- **Formatting vectors** shared by Python and JS.
- **Step gates**: at the end of each step, the reviewer signs off against the section-7 baseline (a checklist file per step in `docs/decisions/`).

---

## 8. Proposed delivery sequence (after approval)
1. Environment + migrations 0001-0003 + seeds (templates, causes, checks, roles) and DDL smoke test on a real PostgreSQL.
2. Step 0 foundation + **Step 1** (import, upload history, mapping, T-03 exceptions) → pilot demo and baseline diagnosis (5.2).
3. Steps 2 → 9 in brief order; WP9 after Q5.

## 9. Decisions I need from you (all have the default shown)
1. **A3** version lifecycle `draft → approved → signed_off` (default: yes).
2. **A4** CHK-06 counts as not passing and blocks sign-off until Q1 is answered (default: yes).
3. **Maker-checker**: adjustment approver must differ from creator (default: yes, configurable).
4. **Rupee-mode display** rounds to whole rupees (matching `₹(4,81,634)`), with checks always exact to the paisa (default: yes).
5. **A6** an explicit "other movements in reserves" input (default 0) in the profit-to-reserves check.
6. **Local PostgreSQL**: none is installed here. Install PostgreSQL 16 (or point me at an existing server) before Step 1 code.
