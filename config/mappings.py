"""
Constants and default mappings for the financial report automation.
"""
from pathlib import Path

# Base paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "instruction_data" / "templates"
DEFAULT_NATURE_LOOKUP = DATA_DIR / "nature_lookup.xlsx"
DEFAULT_OUTPUT_TEMPLATE = DATA_DIR / "output_template.xlsx"

# Bank identifiers
BANK_USD = 29  # USD Bank
BANK_VND = 30  # VND Bank

# Column indices in output template (0-based)
TEMPLATE_COL_VND = 3  # Column D - VND bank (30)
TEMPLATE_COL_USD = 4  # Column E - USD bank (29)

# Row indices in output template (0-based, after header)
TEMPLATE_ROWS = {
    # Income section
    "contribution": 4,
    "fund_transfer": 5,
    "interest": 6,
    "ped": 7,
    # Expenditure by nature section
    "org": 12,
    "edu": 13,
    "oper": 14,
    "nutrition": 15,
    "edu_infra": 16,
    # Advance/Settlement section
    "advance": 36,
    "settlement": 37,
    # Manual input
    "manual": 40,
}

# Nature category mapping (from lookup table to template key)
NATURE_CATEGORY_MAP = {
    "organisational capacity building": "org",
    "education quality improvement": "edu",
    "program operation": "oper",
    "nutrition for the children": "nutrition",
    "education infrastructure": "edu_infra",
    "manual input": "manual",
    "advance by cash": "advance",
    "reimbursement": "settlement",
}

# Transaction type identifiers
TRANSACTION_TYPE_DEPOSIT = "deposit"
TRANSACTION_TYPE_TRANSFER = "transfer"

# Excel parsing configuration
EXCEL_HEADER_ROW = 3  # 0-based index where actual headers are
EXCEL_COLUMNS = {
    "date": 1,
    "transaction_type": 2,
    "name": 5,
    "memo": 6,
    "account": 7,
    "amount": 8,
}

