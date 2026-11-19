"""T-10 Ratios. Every figure comes from the computed Balance Sheet and P&L (never from ledgers or constants).

Definitions live in ratio_definitions (name, display formula, unit, provisional flag); this module is the registry of
calculation keys. A zero denominator yields N/A with a reason, never an error. All ratios are provisional until Q7.
"""
from decimal import ROUND_HALF_UP, Decimal

from app.services import notes_service, statement_service

ZERO = Decimal("0.00")


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


class _Figures:
    def __init__(self, lines: dict):
        self.lines = lines

    def cur(self, code) -> Decimal:
        return _q(self.lines[code]["current"])

    def prev(self, code) -> Decimal:
        return _q(self.lines[code]["previous"])

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


def build(cur, engagement_id: int) -> dict:
    bs = statement_service.balance_sheet(cur, engagement_id)
    pnl = statement_service.profit_and_loss(cur, engagement_id)
    fig = _Figures(notes_service.statement_lines(bs, pnl))
    cur.execute("SELECT * FROM ratio_definitions ORDER BY sort_order")
    items = []
    for d in cur.fetchall():
        num, den, num_label, den_label, note = REGISTRY[d["calc_key"]](fig)
        num, den = _q(num), _q(den)
        item = {"code": d["code"], "name": d["name"], "category": d["category"], "formula": d["formula_display"],
                "unit": d["unit"], "is_provisional": d["is_provisional"], "numerator": num, "denominator": den,
                "numerator_label": num_label, "denominator_label": den_label, "note": note}
        if den == 0:
            item.update(value=None, status="na", explanation=f"N/A: the denominator ({den_label}) is zero.")
        else:
            v = num / den * (100 if d["unit"] == "%" else 1)
            item.update(value=v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), status="ok", explanation=None)
        items.append(item)
    return {"current_label": bs["current_label"], "provisional": any(i["is_provisional"] for i in items),
            "provisional_message": "Ratio list and definitions are provisional pending open question Q7.",
            "items": items, "warnings": bs["warnings"]}
