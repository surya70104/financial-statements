"""Pure tests for the Tally file parser (no database needed): python -m pytest tests/test_parser.py"""
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.tb_parser import ImportFileError, parse_amount, parse_trial_balance

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv"


def column_map():
    sql = (ROOT / "migrations" / "002_seed_step1.sql").read_text(encoding="utf-8")
    block = re.search(r"'TALLY_TB_CSV_V1'.*?'csv',\s*'(\{.*?\})'", sql, re.S).group(1)
    return json.loads(block)


def test_pilot_file_matches_brief_baseline():
    r = parse_trial_balance(PILOT.name, PILOT.read_bytes(), column_map())
    assert r.errors == []
    assert len(r.rows) == 76
    assets = sum(x.closing for x in r.rows if x.nature == "ASSET")
    net = sum(x.closing for x in r.rows if x.nature in ("INCOME", "EXPENSE"))
    assert assets == Decimal("272641256.00")        # 27,26,41,256
    assert net == Decimal("481634.00")              # Dr net = loss of 4,81,634
    assert r.total_debit == r.total_credit
    assert all(not x.notes for x in r.rows)         # every group is a known Tally group


@pytest.mark.parametrize("value,hint,expected", [
    ("1,234.50", None, "1234.50"), ("1234.50 Dr", None, "1234.50"), ("1234.50 Cr", None, "-1234.50"),
    ("500", "Cr", "-500.00"), ("(500)", None, "-500.00"), ("₹2,00,000", "Dr", "200000.00"), ("", None, "0.00"), (None, None, "0.00"),
    (12.5, None, "12.50"), (7, "Cr", "-7.00"),
])
def test_parse_amount(value, hint, expected):
    assert parse_amount(value, hint) == Decimal(expected)


@pytest.mark.parametrize("bad", ["abc", "1.234", "12 Xx"])
def test_parse_amount_rejects(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)


def csv_bytes(*rows):
    head = "Ledger Name,Under Group,Opening Balance,Opening Type,Debit,Credit,Closing Balance,Closing Type\n"
    return (head + "\n".join(rows) + "\n").encode()


def test_closing_mismatch_is_rejected():
    r = parse_trial_balance("x.csv", csv_bytes("Cash,Cash-in-Hand,100,Dr,50,0,999,Dr", "Cap,Capital Account,0,Dr,0,150,150,Cr"), column_map())
    assert r.mismatch_count == 1 and any("does not equal" in e["message"] for e in r.errors)


def test_duplicate_ledger_and_non_footing_are_rejected():
    dup = parse_trial_balance("x.csv", csv_bytes("Cash,Cash-in-Hand,0,Dr,10,0,10,Dr", "  cash ,Cash-in-Hand,0,Dr,5,0,5,Dr"), column_map())
    assert any("Duplicate" in e["message"] for e in dup.errors)
    nofoot = parse_trial_balance("x.csv", csv_bytes("Cash,Cash-in-Hand,0,Dr,10,0,10,Dr", "Cap,Capital Account,0,Dr,0,9,9,Cr"), column_map())
    assert any("does not foot" in e["message"] for e in nofoot.errors)


def test_total_row_skipped_and_unknown_group_inferred():
    r = parse_trial_balance("x.csv", csv_bytes("Cash,Weird Group,0,Dr,10,0,10,Dr", "Cap,Capital Account,0,Dr,0,10,10,Cr", "Grand Total,,,,10,10,,"), column_map())
    assert r.errors == [] and len(r.rows) == 2
    assert r.rows[0].nature == "ASSET" and r.rows[0].notes


def test_missing_columns_rejected():
    with pytest.raises(ImportFileError):
        parse_trial_balance("x.csv", b"Foo,Bar\n1,2\n", column_map())
    with pytest.raises(ImportFileError):
        parse_trial_balance("x.xls", b"junk", column_map())
