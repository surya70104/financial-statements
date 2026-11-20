"""Create the sample workbooks used to try the WP9 modules (TDS/TCS pack, FD register, bank reconciliation, GSTR-9C, LFAR loan schedule):

    .\\.venv\\Scripts\\python.exe -m scripts.make_samples

writes samples/tds_tcs_pack.xlsx, fd_register.xlsx, bank_reconciliation.xlsx, gstr9c_reconciliation.xlsx and loan_schedule.xlsx for the pilot (9AND9 DIGISOFT, FY 2026-27).
Parties and banks are ledgers that already exist in sample_data/9and9_pilot_tb_76_ledgers.csv. Amounts are exact to the
paisa and the accrued interest is calculated with the same function the app uses to verify it.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.services.fd_service import accrued_interest

OUT = Path(__file__).resolve().parent.parent / "samples"
FY_START, FY_END = date(2026, 4, 1), date(2027, 3, 31)
D = Decimal

TDS_HEADERS = ["Entry Type", "Section Code", "Party Name", "Ledger Code", "Base Amount", "Tax Rate (%)", "Tax Amount"]
TDS_ROWS = [
    # type,            section, party,                    ledger (blank = default),     base,       rate,  tax
    ("TDS_PAYABLE",    "194C",  "Cloudnine Hosting",      "TDS Payable - Contractors",  "1250000.00", "2.00",  "25000.00"),
    ("TDS_PAYABLE",    "194J",  "Zenith Consulting",      "TDS Payable - Professionals", "840000.00", "10.00", "84000.00"),
    ("TDS_PAYABLE",    "194J",  "Orbit Recruitment",      "TDS Payable - Professionals", "560000.00", "10.00", "56000.00"),
    ("TDS_PAYABLE",    "194I",  "Infra Partners LLP",     None,                         "1800000.00", "10.00", "180000.00"),
    ("TDS_RECEIVABLE", "194J",  "Acme Retail Pvt Ltd",    "TDS Receivable",             "2400000.00", "10.00", "240000.00"),
    ("TDS_RECEIVABLE", "194C",  "Bharat Logistics Ltd",   "TDS Receivable",             "1000000.00", "2.00",  "20000.00"),
    ("TDS_RECEIVABLE", "194A",  "Loan to Related Party",  "TDS Receivable",              "400000.00", "10.00", "40000.00"),
    ("TCS_PAYABLE",    "206C",  "Evergreen Exports",      None,                         "6200000.00", "0.10",  "6200.00"),
]

FD_HEADERS = ["Bank Name", "FD Number", "Principal Amount", "Interest Rate (%)", "Start Date", "Maturity Date",
              "Maturity Classification", "Accrued Interest", "Schedule Line Code", "Ledger Code"]
FD_ROWS = [
    # bank,        FD number,        principal,   rate,    start,              maturity,           class,        line
    ("HDFC Bank", "50100234567801", "5000000.00", "7.100", date(2026, 4, 15),  date(2027, 4, 15),  "current",     "BS.CA.CASH"),
    ("SBI",       "37821904455",    "8000000.00", "7.000", date(2025, 12, 10), date(2027, 12, 10), "current",     "BS.CA.CASH"),
    ("ICICI Bank", "003405012345",  "3000000.00", "7.250", date(2026, 7, 1),   date(2029, 7, 1),   "non_current", "BS.NCA.OTH"),
    ("HDFC Bank", "50100234567902", "2500000.00", "6.900", date(2026, 1, 20),  date(2027, 7, 20),  "current",     "BS.CA.OTH"),
    ("SBI",       "37821907789",    "6000000.00", "7.150", date(2026, 10, 5),  date(2029, 10, 5),  "non_current", "BS.NCA.OTH"),
]


def _sheet(title, headers, widths):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    return wb, ws


def make_tds():
    wb, ws = _sheet("TDS_TCS", TDS_HEADERS, [16, 13, 26, 28, 16, 13, 16])
    for kind, section, party, ledger, base, rate, tax in TDS_ROWS:
        ws.append([kind, section, party, ledger, float(base), float(rate), float(tax)])
    for r in ws.iter_rows(min_row=2):
        r[4].number_format = r[6].number_format = "#,##0.00"
        r[5].number_format = "0.00"
    wb.save(OUT / "tds_tcs_pack.xlsx")


def make_fd():
    wb, ws = _sheet("FD_Register", FD_HEADERS, [14, 18, 18, 14, 14, 14, 16, 16, 18, 24])
    for bank, number, principal, rate, start, maturity, cls, line in FD_ROWS:
        accrued = accrued_interest(D(principal), D(rate), start, maturity, FY_START, FY_END)
        ws.append([bank, number, float(principal), float(rate), start, maturity, cls, float(accrued), line, None])
    for r in ws.iter_rows(min_row=2):
        r[1].number_format = "@"                                  # FD numbers are text
        r[2].number_format = r[7].number_format = "#,##0.00"
        r[3].number_format = "0.000"
        r[4].number_format = r[5].number_format = "DD-MM-YYYY"
    wb.save(OUT / "fd_register.xlsx")


RECON_HEADERS = ["Type", "Account Name", "Date", "Reference No", "Description", "Book Amount", "Statement Amount", "Difference",
                 "Adjustment Action", "Ledger Code", "Offset Ledger Code"]
BANK = "HDFC Bank Current A/c"
# Amounts are as they move the bank balance: receipts +, payments -. Difference = statement - books.
RECON_ROWS = [
    ("bank", BANK, date(2027, 3, 29), "CHQ 004521", "Cheque issued to Zenith Consulting, not yet presented", "-185000.00", "0.00", "timing_only", None, None),
    ("bank", BANK, date(2027, 3, 31), "DEP 31032027", "Deposit in transit: customer cheque banked on 31 March", "240000.00", "0.00", "timing_only", None, None),
    ("bank", BANK, date(2027, 3, 31), "CHG-Q4", "Bank charges debited by the bank, not yet booked", "0.00", "-4720.00", "propose_adjustment", "Bank Charges", BANK),
    ("bank", BANK, date(2027, 3, 30), "NEFT-CHG", "NEFT / RTGS charges for March, not yet booked", "0.00", "-1180.00", "propose_adjustment", "Bank Charges", BANK),
    ("bank", BANK, date(2027, 3, 31), "FD-INT-Q4", "Quarterly FD interest credited by the bank, not yet booked", "0.00", "38500.00", "propose_adjustment", "Interest on Fixed Deposits", BANK),
    ("bank", BANK, date(2027, 3, 28), "POS-MAR", "POS / payment terminal commission deducted, not yet booked", "0.00", "-7250.40", "propose_adjustment", "Bank Charges", BANK),
]

GSTR_HEADERS = ["GST Head", "Table Ref", "Description", "Books Amount", "GSTR-9C Amount"]
GSTR_ROWS = [
    ("CGST", "Table 9", "Output tax payable per books and per GSTR-9C", "980000.00", "980000.00"),
    ("SGST", "Table 9", "Output tax payable per books and per GSTR-9C", "980000.00", "980000.00"),
    ("IGST", "Table 12", "Input tax credit: books exceed the credit reported in GSTR-9C", "3845000.00", "3797500.00"),
    ("CESS", "Table 12", "Cess credit availed in books but not reported", "18400.00", "12150.00"),
]


def make_recon():
    wb, ws = _sheet("Bank_Reconciliation", RECON_HEADERS, [8, 24, 12, 16, 56, 16, 18, 14, 20, 28, 24])
    for kind, account, when, ref, desc, book, ext, action, ledger, offset in RECON_ROWS:
        ws.append([kind, account, when, ref, desc, float(book), float(ext), float(D(ext) - D(book)), action, ledger, offset if ledger else None])
    for r in ws.iter_rows(min_row=2):
        r[2].number_format = "DD-MM-YYYY"
        for c in (r[5], r[6], r[7]):
            c.number_format = "#,##0.00;(#,##0.00)"
    wb.save(OUT / "bank_reconciliation.xlsx")


def make_gstr():
    wb, ws = _sheet("GSTR9C", GSTR_HEADERS, [10, 12, 60, 18, 18])
    for head, table, desc, books, nine_c in GSTR_ROWS:
        ws.append([head, table, desc, float(books), float(nine_c)])
    for r in ws.iter_rows(min_row=2):
        r[3].number_format = r[4].number_format = "#,##0.00"
    wb.save(OUT / "gstr9c_reconciliation.xlsx")


LOAN_HEADERS = ["Lender Name", "Facility Type", "Account Number", "Ledger Code", "Sanctioned Limit", "Interest Rate (%)", "Security Details", "EMI Status", "Overdue Amount"]
LOAN_ROWS = [
    # Ledger Code is the ledger's name in the trial balance; the balance itself is never in this file (LFAR reads it from the adjusted TB).
    ("HDFC Bank Ltd", "Term Loan", "50300012345678", "Term Loan - HDFC Bank", "45000000.00", "9.250",
     "First charge on book debts and current assets; mortgage of office premises; personal guarantee of directors", "regular", "0.00"),
    ("Kotak Mahindra Prime Ltd", "Vehicle / Equipment Loan", "KMP-VL-228841", "Vehicle Loan", "2500000.00", "8.900", "Hypothecation of the financed vehicles", "regular", "0.00"),
    ("Directors (Mr. A. Rao)", "Unsecured Loan", "UL-DIR-001", "Unsecured Loan - Directors", "20000000.00", "6.000", "Unsecured; repayable on demand; subordinated to bank dues", "regular", "0.00"),
    ("Sunrise Finance Pvt Ltd", "Unsecured Loan", "SFPL-0099", "Unsecured Loan - Others", "4000000.00", "12.000", "Unsecured; no security given", "overdue", "350000.00"),
]


def make_loans():
    wb, ws = _sheet("Loan_Schedule", LOAN_HEADERS, [26, 26, 20, 28, 18, 14, 70, 12, 16])
    for lender, kind, number, ledger, limit, rate, security, emi, overdue in LOAN_ROWS:
        ws.append([lender, kind, number, ledger, float(limit), float(rate), security, emi, float(overdue)])
    for r in ws.iter_rows(min_row=2):
        r[2].number_format = "@"
        r[4].number_format = r[8].number_format = "#,##0.00"
        r[5].number_format = "0.000"
    wb.save(OUT / "loan_schedule.xlsx")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    make_tds()
    make_fd()
    make_recon()
    make_gstr()
    make_loans()
    print(f"Wrote 5 workbooks to {OUT}: tds_tcs_pack ({len(TDS_ROWS)} rows), fd_register ({len(FD_ROWS)}), bank_reconciliation ({len(RECON_ROWS)}), gstr9c_reconciliation ({len(GSTR_ROWS)}), loan_schedule ({len(LOAN_ROWS)})")
