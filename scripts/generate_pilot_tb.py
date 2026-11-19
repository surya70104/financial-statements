"""Generate the realistic pilot Tally trial balance (76 ledgers) for 9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED.

Targets from brief section 7:
  * Total assets (all ASSET-nature ledgers, net) = 27,26,41,256 rupees
  * Net result (INCOME + EXPENSE ledgers)         = loss of 4,81,634 rupees
  * Debits = Credits (the trial balance foots)

Two ledgers are balancing figures (computed here, so the file is exact): the unbilled revenue asset and
opening retained earnings. Run:  python scripts/generate_pilot_tb.py
"""
import csv
from pathlib import Path

TARGET_ASSETS = 272_641_256
TARGET_LOSS = 481_634

# (name, group, signed closing in rupees: Dr +, Cr -)  - None = balancing figure
ASSETS = [
    ("Computers & Laptops", "Fixed Assets", 18_450_000),
    ("Office Equipment", "Fixed Assets", 4_250_000),
    ("Furniture & Fixtures", "Fixed Assets", 3_180_000),
    ("Software & Licences (Capitalised)", "Fixed Assets", 12_600_000),
    ("Accumulated Depreciation - Computers", "Fixed Assets", -5_200_000),
    ("Investment in Mutual Funds", "Investments", 55_000_000),
    ("Investment in Equity - Subsidiary", "Investments", 10_000_000),
    ("Security Deposit - Office Premises", "Deposits (Asset)", 3_600_000),
    ("Security Deposit - Electricity", "Deposits (Asset)", 150_000),
    ("Advance to Employees", "Loans & Advances (Asset)", 420_000),
    ("Loan to Related Party", "Loans & Advances (Asset)", 8_500_000),
    ("Prepaid Expenses", "Loans & Advances (Asset)", 1_260_000),
    ("Advance to Suppliers", "Loans & Advances (Asset)", 2_340_000),
    ("GST Input Credit - IGST", "Current Assets", 3_845_000),
    ("GST Input Credit - CGST", "Current Assets", 1_215_000),
    ("GST Input Credit - SGST", "Current Assets", 1_215_000),
    ("TDS Receivable", "Current Assets", 2_180_000),
    ("Advance Income Tax", "Current Assets", 4_000_000),
    ("Interest Accrued on Fixed Deposits", "Current Assets", 480_000),
    ("Unbilled Revenue", "Current Assets", None),
    ("Acme Retail Pvt Ltd", "Sundry Debtors", 15_600_000),
    ("Bharat Logistics Ltd", "Sundry Debtors", 12_350_000),
    ("Cirrus Media LLP", "Sundry Debtors", 9_420_000),
    ("Delta Healthcare Pvt Ltd", "Sundry Debtors", 11_780_000),
    ("Evergreen Exports", "Sundry Debtors", 7_260_000),
    ("Foundry Analytics Inc", "Sundry Debtors", 18_900_000),
    ("HDFC Bank Current A/c", "Bank Accounts", 21_345_000),
    ("ICICI Bank Current A/c", "Bank Accounts", 9_870_000),
    ("SBI Fixed Deposit", "Bank Accounts", 20_000_000),
    ("Cash in Hand", "Cash-in-Hand", 85_000),
]
LIABILITIES = [  # credit balances are negative
    ("Equity Share Capital", "Capital Account", -100_000),
    ("Securities Premium", "Reserves & Surplus", -149_900_000),
    ("Retained Earnings (Opening)", "Reserves & Surplus", None),
    ("Term Loan - HDFC Bank", "Secured Loans", -40_000_000),
    ("Vehicle Loan", "Secured Loans", -2_400_000),
    ("Unsecured Loan - Directors", "Unsecured Loans", -15_000_000),
    ("Unsecured Loan - Others", "Unsecured Loans", -5_000_000),
    ("Amazon Web Services India", "Sundry Creditors", -6_420_000),
    ("Cloudnine Hosting", "Sundry Creditors", -2_150_000),
    ("Infra Partners LLP", "Sundry Creditors", -3_380_000),
    ("Zenith Consulting", "Sundry Creditors", -1_960_000),
    ("Orbit Recruitment", "Sundry Creditors", -860_000),
    ("Pinnacle Office Supplies", "Sundry Creditors", -340_000),
    ("GST Output - IGST", "Duties & Taxes", -3_120_000),
    ("GST Output - CGST", "Duties & Taxes", -980_000),
    ("GST Output - SGST", "Duties & Taxes", -980_000),
    ("TDS Payable - Salaries", "Duties & Taxes", -1_450_000),
    ("TDS Payable - Professionals", "Duties & Taxes", -620_000),
    ("TDS Payable - Contractors", "Duties & Taxes", -240_000),
    ("PF Payable", "Duties & Taxes", -520_000),
    ("ESI Payable", "Duties & Taxes", -85_000),
    ("Professional Tax Payable", "Duties & Taxes", -42_000),
    ("Provision for Gratuity", "Provisions", -3_200_000),
    ("Provision for Leave Encashment", "Provisions", -1_450_000),
    ("Provision for Audit Fees", "Provisions", -400_000),
    ("Provision for Expenses", "Provisions", -2_150_000),
    ("Salaries Payable", "Current Liabilities", -3_850_000),
    ("Customer Advances", "Current Liabilities", -4_200_000),
    ("Deferred Tax Liability", "Deferred Tax", -1_100_000),
]
INCOME = [
    ("Software Services - Domestic", "Sales Accounts", -62_500_000),
    ("Software Services - Export", "Sales Accounts", -41_250_000),
    ("Subscription Revenue", "Sales Accounts", -9_800_000),
    ("Interest on Fixed Deposits", "Indirect Incomes", -1_480_000),
    ("Foreign Exchange Gain", "Indirect Incomes", -265_000),
]
EXPENSES = [
    ("Cloud Hosting & Infrastructure", "Direct Expenses", 18_600_000),
    ("Subcontractor Charges", "Direct Expenses", 14_350_000),
    ("Salaries & Wages", "Indirect Expenses", 58_200_000),
    ("Employer PF & ESI Contribution", "Indirect Expenses", 4_920_000),
    ("Staff Welfare", "Indirect Expenses", 1_260_000),
    ("Office Rent", "Indirect Expenses", 4_320_000),
    ("Electricity Charges", "Indirect Expenses", 780_000),
    ("Professional & Legal Fees", "Indirect Expenses", 3_150_000),
    ("Audit Fees", "Indirect Expenses", 400_000),
    ("Depreciation", "Indirect Expenses", 5_600_000),
    ("Interest on Term Loan", "Indirect Expenses", 3_860_000),
    ("Bank Charges", "Indirect Expenses", None),
]


def build():
    income_total = -sum(v for _, _, v in INCOME)
    exp_fixed = sum(v for _, _, v in EXPENSES if v is not None)
    exp_plug = income_total + TARGET_LOSS - exp_fixed
    expenses = [(n, g, v if v is not None else exp_plug) for n, g, v in EXPENSES]

    asset_fixed = sum(v for _, _, v in ASSETS if v is not None)
    assets = [(n, g, v if v is not None else TARGET_ASSETS - asset_fixed) for n, g, v in ASSETS]

    pl_net = sum(v for _, _, v in INCOME + expenses)           # = +loss (Dr)
    assert pl_net == TARGET_LOSS, pl_net
    liab_fixed = sum(v for _, _, v in LIABILITIES if v is not None)
    # all ledgers must sum to zero:  assets + liabilities + P&L = 0
    re_plug = -(sum(v for _, _, v in assets) + liab_fixed + pl_net)
    liabs = [(n, g, v if v is not None else re_plug) for n, g, v in LIABILITIES]

    rows = assets + liabs + INCOME + expenses
    assert len(rows) == 76, len(rows)
    assert sum(v for _, _, v in rows) == 0
    assert sum(v for _, _, v in assets) == TARGET_ASSETS
    assert all(v > 0 for n, _, v in assets if n == "Unbilled Revenue")
    assert re_plug < 0, "retained earnings plug must be a credit"
    return rows


def with_openings(rows):
    """Opening = 80% of closing for operating balance-sheet ledgers; capital, premium and retained earnings
    do not move; P&L ledgers open at zero. HDFC Bank opening is the balancing figure so the opening column
    foots to zero as well."""
    fixed_open = {"Equity Share Capital", "Securities Premium", "Retained Earnings (Opening)"}
    pl_groups = {"Sales Accounts", "Indirect Incomes", "Direct Expenses", "Indirect Expenses"}
    plug = "HDFC Bank Current A/c"
    out = {}
    for n, g, c in rows:
        if g in pl_groups:
            out[n] = 0
        elif n in fixed_open:
            out[n] = c
        elif n != plug:
            out[n] = round(c * 0.8)
    out[plug] = -sum(out.values())
    assert out[plug] > 0, out[plug]
    return [(n, g, out[n], c) for n, g, c in rows]


def fmt(amount):
    return f"{abs(amount):.2f}"


def main():
    rows = with_openings(build())
    path = Path(__file__).resolve().parent.parent / "sample_data" / "9and9_pilot_tb_76_ledgers.csv"
    path.parent.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Ledger Name", "Under Group", "Opening Balance", "Opening Type", "Debit", "Credit",
                    "Closing Balance", "Closing Type"])
        for name, group, opening, closing in rows:
            move = closing - opening
            w.writerow([name, group, fmt(opening), "Dr" if opening >= 0 else "Cr",
                        fmt(move) if move > 0 else "0.00", fmt(move) if move < 0 else "0.00",
                        fmt(closing), "Dr" if closing >= 0 else "Cr"])
    print(f"wrote {path}  ({len(rows)} ledgers)")


if __name__ == "__main__":
    main()
