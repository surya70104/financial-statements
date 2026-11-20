"""T-10 Ratios. Every figure comes from the computed Balance Sheet and P&L (never from ledgers or constants).

Definitions live in ratio_definitions (name, display formula, unit, provisional flag); this module is the registry of
calculation keys. A zero denominator yields N/A with a reason, never an error. The nine ratios are the standard Schedule III / ICAI
analytical ratios (confirmed). Each is shown for the current and the previous year, with the % change between them.
"""
from decimal import ROUND_HALF_UP, Decimal

from app.services import notes_service, statement_service

ZERO = Decimal("0.00")


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


class _Figures:
    """The statement figures for ONE year. With prior=True 'current' means the previous year and there is no earlier comparative,
    so averages fall back to the closing balance (and say so)."""

    def __init__(self, lines: dict, prior: bool = False):
        self.lines, self.prior = lines, prior

    def cur(self, code) -> Decimal:
        return _q(self.lines[code]["previous" if self.prior else "current"])

    def prev(self, code) -> Decimal:
        return ZERO if self.prior else _q(self.lines[code]["previous"])

    def label(self, code) -> str:
        return self.lines[code]["label"]

    def average(self, code):
        """Average of opening (previous-period) and closing balance; closing only when there is no comparative."""
        c, p = self.cur(code), self.prev(code)
        if p == 0:
            return c, f"{self.label(code)} (closing balance; no comparative available)"
        return _q((c + p) / 2), f"Average {self.label(code)}"


def _current_ratio(f):
    return f.cur("BS.CA.TOTAL"), f.cur("BS.CL.TOTAL"), f.label("BS.CA.TOTAL"), f.label("BS.CL.TOTAL"), None


def _debt_equity(f):
    return (f.cur("BS.NCL.BORR") + f.cur("BS.CL.BORR"), f.cur("BS.EQ.TOTAL"),
            "Long-term + short-term borrowings", f.label("BS.EQ.TOTAL"), None)


def _dscr(f):
    return (f.cur("PL.PAT") + f.cur("PL.DEP") + f.cur("PL.FIN"), f.cur("PL.FIN"),
            "Profit for the period + Depreciation + Finance costs", "Finance costs",
            "Principal repayments are not available in the trial balance, so debt service is taken as finance costs.")


def _roe(f):
    den, label = f.average("BS.EQ.TOTAL")
    return f.cur("PL.PAT"), den, f.label("PL.PAT"), label, None


def _receivables_turnover(f):
    den, label = f.average("BS.CA.TR")
    return f.cur("PL.REV"), den, f.label("PL.REV"), label, None


def _payables_turnover(f):
    den, label = f.average("BS.CL.TP")
    return f.cur("PL.COGS"), den, f.label("PL.COGS") + " (purchases proxy)", label, \
        "Purchases are not separately recorded; cost of materials and direct expenses is used."


def _net_capital_turnover(f):
    return f.cur("PL.REV"), f.cur("BS.CA.TOTAL") - f.cur("BS.CL.TOTAL"), f.label("PL.REV"), "Working capital (Current assets - Current liabilities)", None


def _net_profit(f):
    return f.cur("PL.PAT"), f.cur("PL.REV"), f.label("PL.PAT"), f.label("PL.REV"), None


def _roce(f):
    ebit = f.cur("PL.PBT") + f.cur("PL.FIN")
    capital = f.cur("BS.EQ.TOTAL") - f.cur("BS.NCA.INTANG") + f.cur("BS.NCL.BORR") + f.cur("BS.CL.BORR") + f.cur("BS.NCL.DTL")
    return ebit, capital, "Profit before tax + Finance costs", "Tangible net worth + Total debt + Deferred tax liability", None


REGISTRY = {"current_ratio": _current_ratio, "debt_equity": _debt_equity, "dscr": _dscr, "roe": _roe,
            "receivables_turnover": _receivables_turnover, "payables_turnover": _payables_turnover,
            "net_capital_turnover": _net_capital_turnover, "net_profit": _net_profit, "roce": _roce}


BASIS = "Standard Schedule III / ICAI Analytical Ratios (Confirmed)"


def _evaluate(calc, fig, unit):
    num, den, num_label, den_label, note = calc(fig)
    num, den = _q(num), _q(den)
    value = None if den == 0 else (num / den * (100 if unit == "%" else 1)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return num, den, num_label, den_label, note, value


def build(cur, engagement_id: int) -> dict:
    bs = statement_service.balance_sheet(cur, engagement_id)
    pnl = statement_service.profit_and_loss(cur, engagement_id)
    lines = notes_service.statement_lines(bs, pnl)
    fig, fig_prior = _Figures(lines), _Figures(lines, prior=True)
    has_prior = any(_q(l["previous"]) != 0 for l in lines.values() if l["kind"] == "mapped")
    cur.execute("SELECT * FROM ratio_definitions ORDER BY sort_order")
    items = []
    for d in cur.fetchall():
        num, den, num_label, den_label, note, value = _evaluate(REGISTRY[d["calc_key"]], fig, d["unit"])
        item = {"code": d["code"], "name": d["name"], "category": d["category"], "formula": d["formula_display"],
                "unit": d["unit"], "is_provisional": d["is_provisional"], "numerator": num, "denominator": den,
                "numerator_label": num_label, "denominator_label": den_label, "note": note}
        if value is None:
            item.update(value=None, status="na", explanation=f"N/A: the denominator ({den_label}) is zero.")
        else:
            item.update(value=value, status="ok", explanation=None)
        pnum = pden = pvalue = None
        if has_prior:
            pnum, pden, _, _, _, pvalue = _evaluate(REGISTRY[d["calc_key"]], fig_prior, d["unit"])
        variance = None
        if value is not None and pvalue is not None and pvalue != 0:
            variance = ((value - pvalue) / abs(pvalue) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)       # % change on last year
        item.update(prior_numerator=pnum, prior_denominator=pden, prior_value=pvalue, variance_pct=variance)
        items.append(item)
    return {"current_label": bs["current_label"], "previous_label": bs["previous_label"], "prior_available": has_prior,
            "provisional": any(i["is_provisional"] for i in items), "basis": BASIS,
            "provisional_message": "Ratio list and definitions are provisional." if any(i["is_provisional"] for i in items) else None,
            "items": items, "warnings": bs["warnings"]}
