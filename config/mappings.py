"""
Constants and default mappings for the financial report automation.
"""

from pathlib import Path

# Base paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "instruction_data" / "templates"
TEMPLATE_DIR = DATA_DIR / "defaults"
DEFAULT_NATURE_LOOKUP = TEMPLATE_DIR / "nature.xlsx"
DEFAULT_OUTPUT_TEMPLATE = DATA_DIR / "output_template.xlsx"
DEFAULT_STAFF_ALLOCATION_LOOKUP = TEMPLATE_DIR / "staff.xlsx"
DEFAULT_PROVINCE_PATTERNS = TEMPLATE_DIR / "province_patterns.xlsx"
# Bank identifiers (string format)
BANK_USD = "29"  # USD Bank
BANK_VND = "30"  # VND Bank
BANK_34 = "34"  # VND Bank (34)

# Column indices in output template (0-based)
TEMPLATE_COL_VND = 3  # Column D - VND bank (30)
TEMPLATE_COL_USD = 4  # Column E - USD bank (29)
TEMPLATE_COL_34 = 5  # Column F - VND bank (34)

# Row indices in output template (0-based, after header)
TEMPLATE_ROWS = {
    # Income section I.A (rows 5-8 in 1-based)
    "contribution": 4,
    "fund_transfer": 5,
    "interest": 6,
    "cash_settlement": 7,
    "income_total": 3,
    # Income section I.B - ELC (row 10 in 1-based)
    "fund_transfer_elc": 9,
    # Expenditure by nature section (rows 13-17 in 1-based)
    "org": 12,
    "edu": 13,
    "oper": 14,
    "nutrition": 15,
    "edu_infra": 16,
    "expense_total": 11,
    # Advance/Settlement/Payable section
    "advance": 36,
    "settlement": 37,
    "payable": 38,  # NEW: Payable section for 1500 accounts
    "advance_settlement_total": 35,
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
    # Mappings for defaults nature.xlsx
    "advance": "advance",
    "settlement": "settlement",
    "payable": "payable",
}

# Nature display names for marked transactions
# Note: "manual" is not included - entries should have actual nature types determined
NATURE_DISPLAY_MAP = {
    "org": "Organisational capacity building",
    "edu": "Education quality improvement",
    "oper": "Program Operation",
    "nutrition": "Nutrition for the children",
    "edu_infra": "Education Infrastructure",
    "advance": "Advance by cash",
    "settlement": "Settlement",
    "payable": "Payable",
}

# Income type display names
INCOME_TYPE_MAP = {
    "contribution": "Contribution",
    "fund_transfer": "Fund transfer to USD",
    "fund_transfer_elc": "Fund transfer to ELC",
    "interest": "Interest",
    "cash_settlement": "Cash settlement",
}

# Province section constants

# Province codes (16 total including manual)
PROVINCE_CODES = [
    "elc",
    "vnelc",
    "vnhbc",
    "vndn",
    "vnqn",
    "vnhd",
    "vnqng",
    "vnmoet",
    "vnbd",
    "vnbg",
    "vnla",
    "vnhcm",
    "vnbn",
    "vnother",
    "caobang",
    "province_manual",
]

# Template row indices for province section (0-based, after header)
PROVINCE_TEMPLATE_ROWS = {
    "elc": 19,
    "vnelc": 20,
    "vnhbc": 21,
    "vndn": 22,
    "vnqn": 23,
    "vnhd": 24,
    "vnqng": 25,
    "vnmoet": 26,
    "vnbd": 27,
    "vnbg": 28,
    "vnla": 29,
    "vnhcm": 30,
    "vnbn": 31,
    "vnother": 32,
    "caobang": 33,
    "province_total": 18,  # Section header row for total
    "province_manual": 41,  # Row 42 for manual province
}

# Province display names for validation column headers
PROVINCE_DISPLAY_MAP = {
    "elc": "ELC Operation",
    "vnelc": "ELC training",
    "vnhbc": "VN General National Training",
    "vndn": "Da Nang ICC",
    "vnqn": "Quang Nam ICC",
    "vnhd": "Hai Duong ICC",
    "vnqng": "Quang Ngai ICC",
    "vnmoet": "Preparation and general of MOET",
    "vnbd": "Binh Duong MOET",
    "vnbg": "Bac Giang MOET",
    "vnla": "Long An MOET",
    "vnhcm": "HCM MOET",
    "vnbn": "Bac Ninh MOET",
    "vnother": "In-country program support",
    "caobang": "Cao Bang",
    "province_manual": "Province Manual",
}
