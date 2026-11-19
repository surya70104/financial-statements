"""Create the two sample workbooks used to try the TDS/TCS pack and the FD register:

    .\\.venv\\Scripts\\python.exe -m scripts.make_samples

writes samples/tds_tcs_pack.xlsx and samples/fd_register.xlsx for the pilot (9AND9 DIGISOFT, FY 2026-27).
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


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    make_tds()
    make_fd()
    print(f"Wrote {OUT / 'tds_tcs_pack.xlsx'} ({len(TDS_ROWS)} rows) and {OUT / 'fd_register.xlsx'} ({len(FD_ROWS)} rows)")
