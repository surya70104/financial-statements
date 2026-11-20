"""Step 1 API: users, engagement header, trial balance import (T-01), mapping (T-02), exceptions (T-03), audit log."""
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.db import tx
from app.jsonutil import json_response
from app.security import current_user, require
from app.services import (adjustments_service, audit_service, checks_service, exceptions_service, issues_service, locking,
                          fd_service, gstr9c_service, mapping_service, materiality_service, notes_service, pack_service, ratios_service, reconciliation_service, signoff_service, statement_service, tds_service, udin_service,
                          tb_import, working_papers_service)

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _engagement_or_404(cur, engagement_id: int) -> dict:
    cur.execute("""SELECT e.id, e.fy_label, e.engagement_type, e.status, e.default_unit, e.data_revision, e.period_start, e.period_end,
                          e.materiality_amount, e.template_confirmed_at, c.name AS client_name, c.entity_type, c.cin, c.pan,
                          t.id AS template_id, t.code AS template_code, t.name AS template_name,
                          v.version_no, v.status AS version_status
                     FROM engagements e JOIN clients c ON c.id = e.client_id
                     JOIN statement_templates t ON t.id = e.template_id
                     LEFT JOIN statement_versions v ON v.id = e.current_version_id
                    WHERE e.id = %s""", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    return eng


@router.get("/users")
def list_users():
    with tx() as cur:
        cur.execute("""SELECT u.id, u.full_name, array_agg(ur.role_code ORDER BY ur.role_code) AS roles
                         FROM users u JOIN user_roles ur ON ur.user_id = u.id
                        WHERE u.is_active AND u.id <> 1 GROUP BY u.id ORDER BY u.id""")
        return json_response(cur.fetchall())


@router.get("/engagements")
def list_engagements():
    with tx() as cur:
        cur.execute("""SELECT e.id, c.name AS client_name, e.fy_label, e.engagement_type, e.status
                         FROM engagements e JOIN clients c ON c.id = e.client_id ORDER BY e.id""")
        return json_response(cur.fetchall())


@router.get("/engagements/{engagement_id}")
def engagement_header(engagement_id: int):
    with tx() as cur:
        eng = _engagement_or_404(cur, engagement_id)
        eng["counts"] = exceptions_service.counts(cur, engagement_id)
        cur.execute("SELECT count(*) AS n FROM ledgers WHERE engagement_id = %s AND is_active", (engagement_id,))
        eng["counts"]["active_ledgers"] = cur.fetchone()["n"]
        eng["checks"] = checks_service.summary(cur, engagement_id)
        eng["issues"] = issues_service.open_counts(cur, engagement_id)
        eng["is_locked"] = locking.is_locked(cur, engagement_id)
        eng["materiality"] = materiality_service.get_config(cur, engagement_id)
        return json_response(eng)


# ---- T-01 ------------------------------------------------------------------------------------------------------
@router.post("/engagements/{engagement_id}/tb/import")
async def import_tb(engagement_id: int, file: UploadFile = File(...), user: dict = Depends(require("tb.import"))):
    data = await file.read()
    if not data:
        raise HTTPException(422, {"message": "The uploaded file is empty.", "errors": []})
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 10 MB.")
    return json_response(tb_import.import_trial_balance(engagement_id, user, file.filename or "upload", data))


@router.get("/engagements/{engagement_id}/tb")
def get_tb(engagement_id: int):
    with tx() as cur:
        _engagement_or_404(cur, engagement_id)
        cur.execute("""SELECT l.id, l.name, l.is_active, g.name AS tally_group, g.nature AS tally_nature,
                              lb.opening_balance, lb.period_debit, lb.period_credit, lb.closing_balance,
                              (m.id IS NOT NULL) AS has_mapping
                         FROM ledgers l
                         LEFT JOIN tally_groups g ON g.id = l.group_id
                         LEFT JOIN ledger_balances lb ON lb.ledger_id = l.id
                         LEFT JOIN ledger_mappings m ON m.ledger_id = l.id
                        WHERE l.engagement_id = %s ORDER BY l.id""", (engagement_id,))
        ledgers = cur.fetchall()
        # This screen shows the trial balance AS IMPORTED (tb_balance); adjustments appear on the Adjustments screen.
        cur.execute("""SELECT coalesce(sum(tb_balance) FILTER (WHERE tb_balance > 0), 0) AS total_debit,
                              coalesce(sum(-tb_balance) FILTER (WHERE tb_balance < 0), 0) AS total_credit,
                              coalesce(sum(tb_balance) FILTER (WHERE tally_nature = 'ASSET'), 0)   AS net_assets,
                              coalesce(sum(-tb_balance) FILTER (WHERE tally_nature = 'LIABILITY'), 0) AS net_liabilities,
                              coalesce(sum(-tb_balance) FILTER (WHERE tally_nature = 'INCOME'), 0)  AS income,
                              coalesce(sum(tb_balance) FILTER (WHERE tally_nature = 'EXPENSE'), 0)  AS expenses,
                              count(*) AS ledger_count
                         FROM v_adjusted_tb WHERE engagement_id = %s""", (engagement_id,))
        s = cur.fetchone()
        summary = {
            "ledger_count": s["ledger_count"],
            "inactive_ledgers": sum(1 for r in ledgers if not r["is_active"]),
            "total_debit": s["total_debit"], "total_credit": s["total_credit"],
            "difference": s["total_debit"] - s["total_credit"],
            "net_assets_by_tally_group": s["net_assets"],
            "net_liabilities_by_tally_group": s["net_liabilities"],
            "income_by_tally_group": s["income"], "expenses_by_tally_group": s["expenses"],
            "net_result_by_tally_group": s["income"] - s["expenses"],
            **exceptions_service.counts(cur, engagement_id),
        }
        cur.execute("""SELECT h.id, h.uploaded_at, h.source_file_name, h.status, h.rows_total, h.ledgers_inserted, h.ledgers_updated,
                              h.ledgers_unchanged, h.ledgers_deactivated, h.total_debit, h.total_credit, h.message,
                              h.error_detail, u.full_name AS uploaded_by
                         FROM upload_history h JOIN users u ON u.id = h.uploaded_by
                        WHERE h.engagement_id = %s ORDER BY h.id DESC LIMIT 100""", (engagement_id,))
        return json_response({"summary": summary, "ledgers": ledgers, "uploads": cur.fetchall()})


# ---- T-02 ------------------------------------------------------------------------------------------------------
class MappingIn(BaseModel):
    line_id: int
    note_id: int | None = None


@router.get("/engagements/{engagement_id}/mappings")
def get_mappings(engagement_id: int):
    with tx() as cur:
        return json_response(mapping_service.get_mappings(cur, engagement_id))


@router.post("/engagements/{engagement_id}/mappings/auto-map")     # declared before /{ledger_id} so it is matched first
def auto_map(engagement_id: int, user: dict = Depends(require("mapping.edit"))):
    with tx(user["id"]) as cur:
        return json_response(mapping_service.auto_map(cur, user, engagement_id))


@router.post("/engagements/{engagement_id}/mappings/{ledger_id}")
def save_mapping(engagement_id: int, ledger_id: int, body: MappingIn, user: dict = Depends(require("mapping.edit"))):
    with tx(user["id"]) as cur:
        return json_response(mapping_service.save_mapping(cur, user, engagement_id, ledger_id, body.line_id, body.note_id))


@router.delete("/engagements/{engagement_id}/mappings/{ledger_id}")
def delete_mapping(engagement_id: int, ledger_id: int, user: dict = Depends(require("mapping.edit"))):
    with tx(user["id"]) as cur:
        return json_response(mapping_service.delete_mapping(cur, user, engagement_id, ledger_id))


# ---- T-03 ------------------------------------------------------------------------------------------------------
@router.get("/engagements/{engagement_id}/exceptions")
def get_exceptions(engagement_id: int, source: str | None = Query("mapping"), status: str | None = Query(None)):
    """T-03 view: the mapping exceptions (default). The unified list of every source is GET .../issues."""
    with tx() as cur:
        _engagement_or_404(cur, engagement_id)
        cur.execute("""SELECT x.id, x.source, x.reference_id, x.cause_code, c.label AS cause, x.title, x.amount, x.status,
                              x.is_condition_active, x.occurrences, x.first_seen_at, x.last_seen_at, x.clear_reason
                         FROM exceptions x JOIN exception_causes c ON c.code = x.cause_code
                        WHERE x.engagement_id = %s AND (%s::text IS NULL OR x.source = %s) AND (%s::text IS NULL OR x.status = %s)
                        ORDER BY x.id""", (engagement_id, source, source, status, status))
        items = cur.fetchall()
        return json_response({"summary": exceptions_service.counts(cur, engagement_id), "items": items})


# ---- T-07 / T-08 -----------------------------------------------------------------------------------------------
@router.get("/engagements/{engagement_id}/statements/balance-sheet")
def get_balance_sheet(engagement_id: int):
    with tx() as cur:
        return json_response(statement_service.balance_sheet(cur, engagement_id))


@router.get("/engagements/{engagement_id}/statements/pnl")
def get_pnl(engagement_id: int):
    with tx() as cur:
        return json_response(statement_service.profit_and_loss(cur, engagement_id))


# ---- T-04 / T-05 / T-06 ----------------------------------------------------------------------------------------
class JournalLineIn(BaseModel):
    ledger_id: int
    debit: str | int | float | None = None
    credit: str | int | float | None = None
    note: str | None = None


class AdjustmentIn(BaseModel):
    entry_date: date
    description: str
    lines: list[JournalLineIn]


@router.get("/engagements/{engagement_id}/adjustments")
def get_adjustments(engagement_id: int):
    with tx() as cur:
        return json_response(adjustments_service.list_entries(cur, engagement_id))


@router.post("/engagements/{engagement_id}/adjustments")
def create_adjustment(engagement_id: int, body: AdjustmentIn, user: dict = Depends(require("adjustments.create"))):
    with tx(user["id"]) as cur:
        return json_response(adjustments_service.create_draft(
            cur, user, engagement_id, body.entry_date, body.description, [l.model_dump() for l in body.lines]))


@router.post("/engagements/{engagement_id}/adjustments/{entry_id}/approve")
def approve_adjustment(engagement_id: int, entry_id: int, user: dict = Depends(require("adjustments.approve"))):
    with tx(user["id"]) as cur:
        return json_response(adjustments_service.approve(cur, user, engagement_id, entry_id))


@router.post("/engagements/{engagement_id}/adjustments/{entry_id}/reverse")
def reverse_adjustment(engagement_id: int, entry_id: int, user: dict = Depends(require("adjustments.create"))):
    with tx(user["id"]) as cur:
        return json_response(adjustments_service.reverse(cur, user, engagement_id, entry_id))


@router.get("/engagements/{engagement_id}/adjusted-tb")
def get_adjusted_tb(engagement_id: int):
    with tx() as cur:
        return json_response(adjustments_service.adjusted_tb(cur, engagement_id))


# ---- T-09 / T-10 -----------------------------------------------------------------------------------------------
@router.get("/engagements/{engagement_id}/notes")
def get_notes(engagement_id: int):
    with tx() as cur:
        return json_response(notes_service.build(cur, engagement_id))


@router.get("/engagements/{engagement_id}/ratios")
def get_ratios(engagement_id: int):
    with tx() as cur:
        return json_response(ratios_service.build(cur, engagement_id))


# ---- T-11 ------------------------------------------------------------------------------------------------------
@router.get("/engagements/{engagement_id}/checks")
def get_checks(engagement_id: int):
    with tx() as cur:
        _engagement_or_404(cur, engagement_id)
        return json_response(checks_service.latest(cur, engagement_id))


@router.post("/engagements/{engagement_id}/checks/run")
def run_checks_now(engagement_id: int, user: dict = Depends(require("checks.run"))):
    with tx(user["id"]) as cur:
        _engagement_or_404(cur, engagement_id)
        res = checks_service.run_checks(cur, engagement_id, "MANUAL")
        audit_service.event(cur, engagement_id, "CHECKS_RUN", res, entity_table="check_runs", entity_id=str(res["run_id"]))
        return json_response(checks_service.latest(cur, engagement_id))


# ---- T-12 unified issue tracker --------------------------------------------------------------------------------
class IssueIn(BaseModel):
    title: str
    detail: str | None = None
    amount: str | int | float | None = None


class ClearIn(BaseModel):
    resolution_note: str | None = None


@router.get("/engagements/{engagement_id}/issues")
def get_issues(engagement_id: int, source: str | None = Query(None), status: str | None = Query(None)):
    with tx() as cur:
        return json_response(issues_service.list_issues(cur, engagement_id, source, status))


@router.post("/engagements/{engagement_id}/issues")
def create_issue(engagement_id: int, body: IssueIn, user: dict = Depends(require("issues.create"))):
    with tx(user["id"]) as cur:
        return json_response(issues_service.create_manual(cur, user, engagement_id, body.title, body.detail, body.amount))


@router.post("/engagements/{engagement_id}/issues/{issue_id}/clear")
def clear_issue(engagement_id: int, issue_id: int, body: ClearIn, user: dict = Depends(require("issues.clear"))):
    with tx(user["id"]) as cur:
        return json_response(issues_service.clear_manual(cur, user, engagement_id, issue_id, body.resolution_note))


# ---- T-13 sign-off gate ----------------------------------------------------------------------------------------
class SignoffIn(BaseModel):
    note: str | None = None


@router.get("/engagements/{engagement_id}/signoff")
def get_signoff(engagement_id: int):
    with tx() as cur:
        return json_response(signoff_service.get_state(cur, engagement_id))


@router.post("/engagements/{engagement_id}/signoff")
def post_signoff(engagement_id: int, body: SignoffIn | None = None, user: dict = Depends(require("signoff.perform"))):
    with tx(user["id"]) as cur:
        return json_response(signoff_service.sign_off(cur, user, engagement_id, body.note if body else None))


class ReopenIn(BaseModel):
    reopen_reason: str | None = None


@router.post("/engagements/{engagement_id}/signoff/reopen")
def post_reopen(engagement_id: int, body: ReopenIn | None = None, user: dict = Depends(require("signoff.reopen"))):
    with tx(user["id"]) as cur:
        return json_response(signoff_service.reopen(cur, user, engagement_id, body.reopen_reason if body else None))


# ---- T-18 UDIN dashboard ---------------------------------------------------------------------------------------
class UdinIn(BaseModel):
    udin_number: str | None = None
    udin_generated_date: str | None = None
    document_type: str | None = None


class UdinRevokeIn(BaseModel):
    revoked_reason: str | None = None


@router.get("/engagements/{engagement_id}/udin")
def get_udin(engagement_id: int):
    with tx() as cur:
        return json_response(udin_service.dashboard(cur, engagement_id))


@router.post("/engagements/{engagement_id}/udin/{version_id}")
def post_udin(engagement_id: int, version_id: int, body: UdinIn, user: dict = Depends(require("udin.manage"))):
    with tx(user["id"]) as cur:
        return json_response(udin_service.record(cur, user, engagement_id, version_id, body.udin_number, body.udin_generated_date, body.document_type))


@router.post("/engagements/{engagement_id}/udin/{version_id}/revoke")
def post_udin_revoke(engagement_id: int, version_id: int, body: UdinRevokeIn, user: dict = Depends(require("udin.manage"))):
    with tx(user["id"]) as cur:
        return json_response(udin_service.revoke(cur, user, engagement_id, version_id, body.revoked_reason))


# ---- T-23 materiality -----------------------------------------------------------------------------------------
class MaterialityIn(BaseModel):
    input_mode: str | None = None
    overall_materiality: str | int | float | None = None
    benchmark_basis: str | None = None
    benchmark_pct: str | int | float | None = None
    performance_materiality: str | int | float | None = None
    trivial_threshold: str | int | float | None = None
    notes: str | None = None


@router.get("/engagements/{engagement_id}/materiality")
def get_materiality(engagement_id: int):
    with tx() as cur:
        return json_response(materiality_service.get(cur, engagement_id))


@router.post("/engagements/{engagement_id}/materiality")
def post_materiality(engagement_id: int, body: MaterialityIn, user: dict = Depends(require("materiality.set"))):
    with tx(user["id"]) as cur:
        return json_response(materiality_service.save(cur, user, engagement_id, body.model_dump()))


# ---- T-19 TDS / TCS entry pack and T-22 fixed deposit register ---------------------------------------------------
class TdsRowIn(BaseModel):
    entry_type: str | None = None
    section_code: str | None = None
    party_name: str | None = None
    ledger_code: str | None = None
    base_amount: str | int | float | None = None
    tax_rate: str | int | float | None = None
    tax_amount: str | int | float | None = None


class FdRowIn(BaseModel):
    bank_name: str | None = None
    fd_number: str | int | None = None
    ledger_code: str | None = None
    principal_amount: str | int | float | None = None
    interest_rate: str | int | float | None = None
    start_date: str | None = None
    maturity_date: str | None = None
    maturity_classification: str | None = None
    accrued_interest: str | int | float | None = None
    schedule_line_code: str | None = None


class PostIn(BaseModel):
    ids: list[int] | None = None


async def _upload(file: UploadFile) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(422, {"message": "The uploaded file is empty.", "errors": []})
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 10 MB.")
    return data


@router.get("/engagements/{engagement_id}/tds-tcs")
def get_tds(engagement_id: int):
    with tx() as cur:
        return json_response(tds_service.list_rows(cur, engagement_id))


@router.post("/engagements/{engagement_id}/tds-tcs/import")
async def import_tds(engagement_id: int, file: UploadFile = File(...), user: dict = Depends(require("tds.edit"))):
    data = await _upload(file)
    with tx(user["id"]) as cur:
        return json_response(tds_service.import_file(cur, user, engagement_id, file.filename or "upload", data))


@router.post("/engagements/{engagement_id}/tds-tcs/post")
def post_tds(engagement_id: int, body: PostIn | None = None, user: dict = Depends(require("tds.post"))):
    with tx(user["id"]) as cur:
        return json_response(tds_service.post(cur, user, engagement_id, body.ids if body else None))


@router.post("/engagements/{engagement_id}/tds-tcs")
def add_tds(engagement_id: int, body: TdsRowIn, user: dict = Depends(require("tds.edit"))):
    with tx(user["id"]) as cur:
        return json_response(tds_service.add_row(cur, user, engagement_id, body.model_dump()))


@router.delete("/engagements/{engagement_id}/tds-tcs/{row_id}")
def delete_tds(engagement_id: int, row_id: int, user: dict = Depends(require("tds.edit"))):
    with tx(user["id"]) as cur:
        return json_response(tds_service.delete_row(cur, user, engagement_id, row_id))


@router.get("/engagements/{engagement_id}/fixed-deposits")
def get_fds(engagement_id: int):
    with tx() as cur:
        return json_response(fd_service.list_rows(cur, engagement_id))


@router.post("/engagements/{engagement_id}/fixed-deposits/import")
async def import_fds(engagement_id: int, file: UploadFile = File(...), user: dict = Depends(require("fd.edit"))):
    data = await _upload(file)
    with tx(user["id"]) as cur:
        return json_response(fd_service.import_file(cur, user, engagement_id, file.filename or "upload", data))


@router.post("/engagements/{engagement_id}/fixed-deposits/post")
def post_fds(engagement_id: int, body: PostIn | None = None, user: dict = Depends(require("fd.post"))):
    with tx(user["id"]) as cur:
        return json_response(fd_service.post(cur, user, engagement_id, body.ids if body else None))


@router.post("/engagements/{engagement_id}/fixed-deposits")
def add_fd(engagement_id: int, body: FdRowIn, user: dict = Depends(require("fd.edit"))):
    with tx(user["id"]) as cur:
        return json_response(fd_service.add_row(cur, user, engagement_id, body.model_dump()))


@router.delete("/engagements/{engagement_id}/fixed-deposits/{row_id}")
def delete_fd(engagement_id: int, row_id: int, user: dict = Depends(require("fd.edit"))):
    with tx(user["id"]) as cur:
        return json_response(fd_service.delete_row(cur, user, engagement_id, row_id))


# ---- T-20 reconciliation engine and T-21 GSTR-9C reconciliation -------------------------------------------------
class ReconIn(BaseModel):
    recon_type: str | None = None
    account_name: str | None = None
    item_date: str | None = None
    reference_no: str | int | None = None
    description: str | None = None
    book_amount: str | int | float | None = None
    external_amount: str | int | float | None = None
    adjustment_action: str | None = None
    ledger_code: str | None = None
    offset_ledger_code: str | None = None


class Gstr9cIn(BaseModel):
    gst_head: str | None = None
    table_ref: str | None = None
    description: str | None = None
    books_amount: str | int | float | None = None
    gstr9c_amount: str | int | float | None = None


@router.get("/engagements/{engagement_id}/reconciliations")
def get_recon(engagement_id: int):
    with tx() as cur:
        return json_response(reconciliation_service.list_rows(cur, engagement_id))


@router.post("/engagements/{engagement_id}/reconciliations/import")
async def import_recon(engagement_id: int, file: UploadFile = File(...), user: dict = Depends(require("recon.edit"))):
    data = await _upload(file)
    with tx(user["id"]) as cur:
        return json_response(reconciliation_service.import_file(cur, user, engagement_id, file.filename or "upload", data))


@router.post("/engagements/{engagement_id}/reconciliations/propose-adjustments")
def propose_recon(engagement_id: int, user: dict = Depends(require("recon.propose"))):
    with tx(user["id"]) as cur:
        return json_response(reconciliation_service.propose(cur, user, engagement_id))


@router.post("/engagements/{engagement_id}/reconciliations")
def add_recon(engagement_id: int, body: ReconIn, user: dict = Depends(require("recon.edit"))):
    with tx(user["id"]) as cur:
        return json_response(reconciliation_service.add_row(cur, user, engagement_id, body.model_dump()))


@router.delete("/engagements/{engagement_id}/reconciliations/{item_id}")
def delete_recon(engagement_id: int, item_id: int, user: dict = Depends(require("recon.edit"))):
    with tx(user["id"]) as cur:
        return json_response(reconciliation_service.delete_row(cur, user, engagement_id, item_id))


@router.get("/engagements/{engagement_id}/gstr9c")
def get_gstr9c(engagement_id: int):
    with tx() as cur:
        return json_response(gstr9c_service.list_rows(cur, engagement_id))


@router.post("/engagements/{engagement_id}/gstr9c/import")
async def import_gstr9c(engagement_id: int, file: UploadFile = File(...), user: dict = Depends(require("gstr9c.edit"))):
    data = await _upload(file)
    with tx(user["id"]) as cur:
        return json_response(gstr9c_service.import_file(cur, user, engagement_id, file.filename or "upload", data))


@router.post("/engagements/{engagement_id}/gstr9c")
def add_gstr9c(engagement_id: int, body: Gstr9cIn, user: dict = Depends(require("gstr9c.edit"))):
    with tx(user["id"]) as cur:
        return json_response(gstr9c_service.add_row(cur, user, engagement_id, body.model_dump()))


@router.delete("/engagements/{engagement_id}/gstr9c/{row_id}")
def delete_gstr9c(engagement_id: int, row_id: int, user: dict = Depends(require("gstr9c.edit"))):
    with tx(user["id"]) as cur:
        return json_response(gstr9c_service.delete_row(cur, user, engagement_id, row_id))


# ---- T-14 working papers ---------------------------------------------------------------------------------------
class WorkingPaperIn(BaseModel):
    target_type: str
    target_code: str
    wp_ref_code: str
    wp_title: str
    wp_url_or_path: str | None = None
    notes: str | None = None


@router.get("/engagements/{engagement_id}/working-papers")
def get_working_papers(engagement_id: int):
    with tx() as cur:
        return json_response(working_papers_service.list_links(cur, engagement_id))


@router.post("/engagements/{engagement_id}/working-papers")
def attach_working_paper(engagement_id: int, body: WorkingPaperIn, user: dict = Depends(require("wp.attach"))):
    with tx(user["id"]) as cur:
        return json_response(working_papers_service.attach(cur, user, engagement_id, body.target_type, body.target_code, body.wp_ref_code,
                                                           body.wp_title, body.wp_url_or_path, body.notes))


@router.post("/engagements/{engagement_id}/working-papers/{wp_id}/review")
def review_working_paper(engagement_id: int, wp_id: int, user: dict = Depends(require("wp.review"))):
    with tx(user["id"]) as cur:
        return json_response(working_papers_service.review(cur, user, engagement_id, wp_id))


@router.delete("/engagements/{engagement_id}/working-papers/{wp_id}")
def remove_working_paper(engagement_id: int, wp_id: int, user: dict = Depends(require("wp.attach"))):
    with tx(user["id"]) as cur:
        return json_response(working_papers_service.remove(cur, user, engagement_id, wp_id))


# ---- T-16 audit committee / IA pack ----------------------------------------------------------------------------
class PackExportIn(BaseModel):
    format: str


@router.get("/engagements/{engagement_id}/pack")
def get_pack(engagement_id: int):
    with tx() as cur:
        return json_response(pack_service.build(cur, engagement_id))


@router.post("/engagements/{engagement_id}/pack/export")
def log_pack_export(engagement_id: int, body: PackExportIn, user: dict = Depends(require("pack.export"))):
    with tx(user["id"]) as cur:
        return json_response(pack_service.log_export(cur, user, engagement_id, body.format))


# ---- audit trail (read only) -----------------------------------------------------------------------------------
@router.get("/engagements/{engagement_id}/audit-logs")
def get_audit(engagement_id: int, limit: int = Query(100, ge=1, le=500), scope: str = Query("default", pattern="^(default|events|all)$"),
              entity_table: str | None = Query(None), before_id: int | None = Query(None), _user: dict = Depends(current_user)):
    """Newest first. scope: default (business events + mapping/exception changes, the Step 1 view), events (business events only),
    all (every recorded change for the engagement). Page back with before_id = the smallest id already loaded."""
    with tx() as cur:
        _engagement_or_404(cur, engagement_id)
        cur.execute("""SELECT a.id, a.occurred_at, u.full_name AS user_name, a.entity_table, a.entity_id, a.action, a.event_name,
                              a.changed_fields, a.before_data, a.after_data, a.reason
                         FROM audit_logs a JOIN users u ON u.id = a.user_id
                        WHERE a.engagement_id = %(e)s
                          AND (%(scope)s = 'all' OR a.action = 'EVENT'
                               OR (%(scope)s = 'default' AND a.entity_table IN ('ledger_mappings','exceptions')))
                          AND (%(t)s::text IS NULL OR a.entity_table = %(t)s)
                          AND (%(b)s::bigint IS NULL OR a.id < %(b)s)
                        ORDER BY a.id DESC LIMIT %(l)s""", {"e": engagement_id, "scope": scope, "t": entity_table, "b": before_id, "l": limit})
        return json_response(cur.fetchall())
