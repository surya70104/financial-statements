"""Entity type (Q2): what changes between a Private Limited Company (Schedule III, Division I) and a Partnership Firm.

Only the equity presentation differs: a partnership has Partners' Capital and Current Accounts, not Share capital and Reserves
and surplus. The same lines, notes, mappings and totals are used, so nothing else in the statements moves.
"""
ENTITY_TYPES = ("private_limited", "partnership")
ENTITY_LABELS = {"private_limited": "Private Limited Company (Schedule III)", "partnership": "Partnership Firm"}
SHORT_LABELS = {"private_limited": "Private Limited Company", "partnership": "Partnership Firm"}

# template line code -> label shown for a partnership
LINE_LABELS = {"partnership": {"BS.EQ.SC": "Partners' Capital Account", "BS.EQ.RS": "Partners' Current Account", "BS.EQ.TOTAL": "Total partners' funds"}}
# the notes that sit on those lines
NOTE_TITLES = {"partnership": {"BS.EQ.SC": "Partners' capital account", "BS.EQ.RS": "Partners' current account"}}
SIGNATORIES = {"private_limited": "Directors", "partnership": "Partners"}


def line_label(entity_type: str, code: str, default: str) -> str:
    return LINE_LABELS.get(entity_type, {}).get(code, default)


def note_title(entity_type: str, line_code: str, default: str) -> str:
    return NOTE_TITLES.get(entity_type, {}).get(line_code, default)
