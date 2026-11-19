"""T-23 Materiality: set per engagement, and flag every statement line whose year-on-year change is above it.

Overall materiality is typed in ('manual') or calculated as benchmark x % ('calculated'), in exact Decimal paise.
The benchmark figure is never typed: it is read live from the statements (revenue, total assets or total expenses).
Performance materiality defaults to 75% and the clearly-trivial threshold to 5% of overall materiality.

A line is flagged when |current - previous| > overall materiality, on the mapped (detail) lines, and only when the
engagement has prior-year comparatives. Each flagged line is ONE deduplicated 'materiality' issue in the tracker; it
re-opens if the facts change and clears itself when the variance falls within materiality (or materiality is raised).
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException

from app.services import audit_service, locking, notes_service, statement_service

CENT = Decimal("0.01")
BASES = {"revenue": ("PL.REV", "Revenue from operations"), "total_assets": ("BS.ASSETS.TOTAL", "Total assets"),
         "expenses": ("PL.EXP.TOTAL", "Total expenses")}
PERFORMANCE_PCT, TRIVIAL_PCT = Decimal("75"), Decimal("5")


def _q(v) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def _inr(v: Decimal) -> str:
    """Indian digit grouping with exact paise: 27,26,41,256.00 (negative in brackets)."""
    neg, v = v < 0, abs(_q(v))
    whole, frac = f"{v:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:]); head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join(groups + [tail]) + "." + frac
    return f"₹({text})" if neg else f"₹{text}"


def _amount(value, label: str, required: bool = True):
    if value in (None, ""):
        if required:
            raise HTTPException(400, f"Enter {label}.")
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise HTTPException(400, f"'{value}' is not a valid amount for {label}.")
    if not d.is_finite() or d != d.quantize(CENT):
        raise HTTPException(400, f"{label[0].upper() + label[1:]} must be exact to the paisa (2 decimals).")
    if d <= 0:
        raise HTTPException(400, f"{label[0].upper() + label[1:]} must be greater than zero.")
    return d.quantize(CENT)                                          # always exactly 2 decimals, also in the audit event


def _statements(cur, engagement_id: int):
    bs = statement_service.balance_sheet(cur, engagement_id)
    pnl = statement_service.profit_and_loss(cur, engagement_id)
    return bs, pnl, notes_service.statement_lines(bs, pnl)


def _benchmarks(lines: dict) -> dict:
    return {k: {"label": label, "code": code, "amount": lines[code]["current"]} for k, (code, label) in BASES.items()}


def _variances(lines: dict) -> tuple[list, bool]:
    """Year-on-year change of every mapped line, largest first. Second value: do prior-year comparatives exist at all?"""
    rows = [l for l in lines.values() if l["kind"] == "mapped"]
    has_comparatives = any(l["previous"] != 0 for l in rows)
    out = []
    for l in rows:
        change = _q(l["current"] - l["previous"])
        if has_comparatives and change != 0:
            out.append({"code": l["code"], "label": l["label"], "note_no": l["note_no"], "current": l["current"], "previous": l["previous"],
                        "change": change, "variance": abs(change)})
    out.sort(key=lambda r: (-r["variance"], r["code"]))
    return out, has_comparatives


def get_config(cur, engagement_id: int):
    cur.execute("""SELECT id, engagement_id, input_mode, benchmark_basis, benchmark_pct, benchmark_amount, overall_materiality,
                          performance_materiality, trivial_threshold, set_by, set_at, notes
                     FROM engagement_materiality WHERE engagement_id = %s""", (engagement_id,))
    return cur.fetchone()


def get(cur, engagement_id: int) -> dict:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    cfg = get_config(cur, engagement_id)
    _, _, lines = _statements(cur, engagement_id)
    variances, has_comparatives = _variances(lines)
    exceeding = [v for v in variances if cfg and v["variance"] > cfg["overall_materiality"]]
    return {"materiality": cfg, "benchmarks": _benchmarks(lines),
            "defaults": {"performance_pct": PERFORMANCE_PCT, "trivial_pct": TRIVIAL_PCT},
            "comparatives_available": has_comparatives, "variances": variances, "exceeding": exceeding}


def save(cur, user: dict, engagement_id: int, body: dict) -> dict:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)
    mode = (body.get("input_mode") or "").strip()
    pct = bench_amount = None
    basis = "manual"
    if mode == "manual":
        overall = _amount(body.get("overall_materiality"), "overall materiality")
    elif mode == "calculated":
        basis = (body.get("benchmark_basis") or "").strip()
        if basis not in BASES:
            raise HTTPException(400, "Choose the benchmark: revenue, total_assets or expenses.")
        pct = _amount(body.get("benchmark_pct"), "the benchmark percentage")
        if pct > 100 or pct != pct.quantize(CENT):
            raise HTTPException(400, "The benchmark percentage must be between 0.01 and 100.")
        _, _, lines = _statements(cur, engagement_id)
        bench_amount = _benchmarks(lines)[basis]["amount"]
        if bench_amount <= 0:
            raise HTTPException(400, f"The {BASES[basis][1].lower()} benchmark is not positive, so a percentage of it cannot be used.")
        overall = _q(bench_amount * pct / 100)                       # exact Decimal, rounded half-up to the paisa
        if overall <= 0:
            raise HTTPException(400, "That percentage of the benchmark rounds to zero.")
    else:
        raise HTTPException(400, "input_mode must be 'manual' or 'calculated'.")

    perf = _amount(body.get("performance_materiality"), "performance materiality", required=False) or _q(overall * PERFORMANCE_PCT / 100)
    triv = _amount(body.get("trivial_threshold"), "the clearly trivial threshold", required=False) or _q(overall * TRIVIAL_PCT / 100)
    if perf > overall:
        raise HTTPException(400, "Performance materiality cannot exceed overall materiality.")
    if triv > overall:
        raise HTTPException(400, "The clearly trivial threshold cannot exceed overall materiality.")
    notes = (body.get("notes") or "").strip() or None
    previous = get_config(cur, engagement_id)
    cur.execute("""INSERT INTO engagement_materiality (engagement_id, input_mode, benchmark_basis, benchmark_pct, benchmark_amount,
                          overall_materiality, performance_materiality, trivial_threshold, set_by, set_at, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                   ON CONFLICT (engagement_id) DO UPDATE SET input_mode = EXCLUDED.input_mode, benchmark_basis = EXCLUDED.benchmark_basis,
                          benchmark_pct = EXCLUDED.benchmark_pct, benchmark_amount = EXCLUDED.benchmark_amount,
                          overall_materiality = EXCLUDED.overall_materiality, performance_materiality = EXCLUDED.performance_materiality,
                          trivial_threshold = EXCLUDED.trivial_threshold, set_by = EXCLUDED.set_by, set_at = now(), notes = EXCLUDED.notes""",
                (engagement_id, mode, basis, pct, bench_amount, overall, perf, triv, user["full_name"][:64], notes))
    flagged = sync_issues(cur, engagement_id)
    audit_service.event(cur, engagement_id, "MATERIALITY_SET",
                        {"input_mode": mode, "benchmark_basis": basis, "benchmark_pct": pct, "benchmark_amount": bench_amount,
                         "overall_materiality": overall, "performance_materiality": perf, "trivial_threshold": triv,
                         "previous_overall_materiality": previous["overall_materiality"] if previous else None,
                         "set_by": user["full_name"], "lines_above_materiality": flagged},
                        entity_table="engagement_materiality", reason=notes)
    return get(cur, engagement_id)


_UPSERT = """
INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, detail, amount, fingerprint)
VALUES (%(e)s, 'materiality', %(ref)s, 'ABOVE_MATERIALITY', %(title)s, %(detail)s, %(amount)s, md5(%(fp)s))
ON CONFLICT (engagement_id, source, reference_id) DO UPDATE SET
       title = EXCLUDED.title, detail = EXCLUDED.detail, amount = EXCLUDED.amount, fingerprint = EXCLUDED.fingerprint,
       is_condition_active = TRUE, status = 'open',
       cleared_by = NULL, cleared_by_system = FALSE, cleared_at = NULL, clear_reason = NULL,
       approved_by = NULL, approved_at = NULL, approval_reason = NULL,
       last_seen_at = now(), occurrences = exceptions.occurrences + 1
 WHERE NOT exceptions.is_condition_active OR exceptions.fingerprint <> EXCLUDED.fingerprint
"""

_CLOSE = """
UPDATE exceptions e SET
       is_condition_active = FALSE,
       status            = CASE WHEN e.status = 'open' THEN 'cleared' ELSE e.status END,
       cleared_by_system = CASE WHEN e.status = 'open' THEN TRUE ELSE e.cleared_by_system END,
       cleared_at        = CASE WHEN e.status = 'open' THEN now() ELSE e.cleared_at END,
       clear_reason      = CASE WHEN e.status = 'open' THEN 'Auto-cleared: the change is now within materiality' ELSE e.clear_reason END
 WHERE e.engagement_id = %(e)s AND e.source = 'materiality' AND e.is_condition_active AND e.reference_id <> ALL(%(keep)s)
"""


def sync_issues(cur, engagement_id: int) -> int:
    """Make the tracker match the current statements and materiality. Returns how many lines are above materiality.
    Idempotent: unchanged issues are not touched, so re-running adds no rows and no audit noise."""
    cfg = get_config(cur, engagement_id)
    keep, flagged = [], []
    if cfg:
        _, _, lines = _statements(cur, engagement_id)
        variances, _ = _variances(lines)
        flagged = [v for v in variances if v["variance"] > cfg["overall_materiality"]]
        cur.execute("SELECT fy_label FROM engagements WHERE id = %s", (engagement_id,))
        cur_label = f"FY {cur.fetchone()['fy_label']}"
        for v in flagged:
            ref = "MATERIALITY:" + v["code"]
            keep.append(ref)
            detail = (f"{cur_label} {_inr(v['current'])} against prior year {_inr(v['previous'])}: a change of {_inr(v['change'])} "
                      f"is above overall materiality of {_inr(cfg['overall_materiality'])}.")
            cur.execute(_UPSERT, {"e": engagement_id, "ref": ref, "title": f"Above materiality: {v['label']}", "detail": detail,
                                  "amount": v["variance"],
                                  "fp": f"{v['code']}|{v['current']}|{v['previous']}|{cfg['overall_materiality']}"})
    cur.execute(_CLOSE, {"e": engagement_id, "keep": keep})
    return len(flagged)
