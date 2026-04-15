"""
Shared utility functions for calculators.

Consolidates common operations like exchange rate handling and amount conversion
to eliminate code duplication across calculator modules.
"""

import logging
import re
from typing import Optional, Union
from pathlib import Path
from datetime import datetime
from io import BytesIO

import pandas as pd

from ..models import TransactionGroup, BANK_USD, BANK_VND

logger = logging.getLogger(__name__)


def get_exchange_rate(
    group: TransactionGroup, exchange_rates: Optional[dict[str, float]] = None
) -> float:
    """
    Get exchange rate for a transaction group.

    Returns 1.0 for VND bank (no conversion needed).
    For USD bank, looks up rate by group_id, then date, then group.exchange_rate.

    Args:
        group: Transaction group
        exchange_rates: Dictionary of group_id or date string to exchange rate

    Returns:
        Exchange rate (1.0 if not applicable or not found)
    """
    if group.bank_identifier != BANK_USD:
        return 1.0
    if exchange_rates:
        # Try group_id first (per-transaction rates)
        if group.group_id in exchange_rates:
            return exchange_rates[group.group_id]
        # Fall back to date key (legacy per-date rates)
        if group.date:
            date_key = group.date.strftime("%Y-%m-%d")
            if date_key in exchange_rates:
                return exchange_rates[date_key]
    # Fall back to rate stored on group (set before transfer splitting)
    if group.exchange_rate and group.exchange_rate != 1.0:
        return group.exchange_rate
    return 1.0


def convert_amount(amount: float, bank_identifier: str, ex_rate: float) -> float:
    """
    Convert amount using exchange rate (divide for VND to USD).

    For USD bank, divides amount by exchange rate.
    For VND bank, returns amount as-is.

    Args:
        amount: Amount to convert
        bank_identifier: "29" for USD or "30" for VND
        ex_rate: Exchange rate

    Returns:
        Converted amount
    """
    if bank_identifier == BANK_USD and ex_rate > 0:
        return amount / ex_rate
    return amount


def init_nature_totals() -> dict[str, float]:
    """Initialize nature totals dictionary with all categories."""
    return {
        "org": 0.0,
        "edu": 0.0,
        "oper": 0.0,
        "nutrition": 0.0,
        "edu_infra": 0.0,
        "manual": 0.0,
    }


def init_advance_settlement_totals() -> dict[str, float]:
    """Initialize advance/settlement totals dictionary."""
    return {
        "advance": 0.0,
        "settlement": 0.0,
    }


def init_payable_totals() -> dict[str, float]:
    """Initialize payable totals dictionary (for 1500 accounts)."""
    return {
        "payable": 0.0,
    }


def init_income_totals() -> dict[str, float]:
    """Initialize income totals dictionary."""
    return {
        "contribution": 0.0,
        "fund_transfer": 0.0,
        "fund_transfer_elc": 0.0,
        "interest": 0.0,
        "cash_settlement": 0.0,
    }


# Account type constants
ACCOUNT_TYPES = ("1250", "1230", "1252", "1500")


def get_account_type(account_number: Optional[str]) -> Optional[str]:
    """
    Determine account type from account number string.

    Args:
        account_number: Account number string

    Returns:
        Account type ("1250", "1230", "1252", "1500") or None
    """
    if not account_number:
        return None
    account_lower = account_number.lower()
    for acct_type in ACCOUNT_TYPES:
        if acct_type in account_lower:
            return acct_type
    return None


# Nature normalization mapping
NATURE_NORMALIZATION_MAP = {
    "advance": "advance",
    "settlement": "settlement",
    "payable": "payable",
    "org": "org",
    "edu": "edu",
    "oper": "oper",
    "nutrition": "nutrition",
    "infra": "edu_infra",
    # Mappings for defaults nature.xlsx values
    "organisational capacity building": "org",
    "education quality improvement": "edu",
    "program operation": "oper",
}


def normalize_nature(nature_raw: str) -> str:
    """
    Normalize nature string to category key.

    Args:
        nature_raw: Raw nature string from lookup

    Returns:
        Normalized nature key
    """
    nature_lower = nature_raw.lower().strip()

    for pattern, category in NATURE_NORMALIZATION_MAP.items():
        if pattern in nature_lower:
            return category

    return nature_lower


def load_staff_allocation_data(
    source: Union[str, Path, BytesIO],
) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    """
    Load staff lookup and allocation tables from staff.xlsx.

    Structure (defaults):
    - Sheet: "Lookup3_Staff & allocation"
    - Header row: 1 (0-indexed)
    - Column A (0): Staff Name
    - Column B (1): PACCOM nature (org/edu)
    - Columns C-K (2-10): Province allocations

    Args:
        source: Path to staff.xlsx or file-like object.

    Returns:
        Tuple of (staff_lookup, allocation_lookup)
        - staff_lookup: dict[name_lower] -> nature
        - allocation_lookup: dict[name_lower] -> {province: percentage}
    """
    staff_table: dict[str, str] = {}
    allocation_table: dict[str, dict[str, float]] = {}

    try:
        df = pd.read_excel(source, sheet_name="Lookup3_Staff & allocation", header=None)
        logger.debug("Loading staff.xlsx, shape: %s", df.shape)

        # Find header row (row with "Staff Name")
        header_idx = 1  # Default for defaults format
        for idx in range(min(10, len(df))):
            for val in df.iloc[idx].values:
                if isinstance(val, str) and "staff name" in val.lower():
                    header_idx = idx
                    break

        # Get province column names from header row
        header_row = df.iloc[header_idx]
        province_cols: dict[int, str] = {}
        for col_idx in range(2, len(header_row)):
            col_name = header_row.iloc[col_idx]
            if pd.notna(col_name) and str(col_name).strip():
                province_cols[col_idx] = str(col_name).strip().lower()

        logger.debug("Province columns: %s", province_cols)

        # Parse data rows
        for idx in range(header_idx + 1, len(df)):
            row = df.iloc[idx]
            name = row.iloc[0] if len(row) > 0 else None
            nature_raw = row.iloc[1] if len(row) > 1 else None

            if pd.isna(name) or str(name).strip() == "":
                continue

            name_key = str(name).strip().lower()

            # Staff nature lookup
            if pd.notna(nature_raw):
                nature = normalize_nature(str(nature_raw))
                staff_table[name_key] = nature

            # Allocation percentages
            allocations = {}
            for col_idx, province_code in province_cols.items():
                val = row.iloc[col_idx] if len(row) > col_idx else 0
                if pd.notna(val) and val != 0:
                    try:
                        allocations[province_code] = float(val)
                    except (ValueError, TypeError):
                        pass

            if allocations:
                allocation_table[name_key] = allocations

        logger.info(
            "Loaded %d staff entries, %d allocation entries",
            len(staff_table),
            len(allocation_table),
        )

    except Exception as e:
        logger.warning("Could not load staff allocation table: %s", e)

    return staff_table, allocation_table


def _load_allocation_legacy(
    source: Union[str, Path, BytesIO],
) -> dict[str, dict[str, float]]:
    """Load allocation table from legacy format (salary allocation-v2 sheet)."""
    allocation_table: dict[str, dict[str, float]] = {}

    try:
        df = pd.read_excel(source, sheet_name="salary allocation-v2", header=None)
        logger.debug("Loading allocation.xlsx (legacy)")

        province_cols = {
            4: "vnelc",
            5: "vndn",
            6: "vnqn",
            7: "vnhd",
            8: "vnqng",
            9: "vnmoet",
        }

        header_row = 4
        for idx in range(header_row + 1, len(df)):
            row = df.iloc[idx]
            name = row.iloc[1]

            if pd.isna(name) or str(name).strip() == "":
                continue

            allocations = {}
            for col_idx, province_key in province_cols.items():
                val = row.iloc[col_idx] if len(row) > col_idx else 0
                if pd.notna(val) and val != 0:
                    try:
                        allocations[province_key] = float(val)
                    except (ValueError, TypeError):
                        pass

            if allocations:
                allocation_table[str(name).strip().lower()] = allocations

        logger.info("Loaded %d allocation entries (legacy)", len(allocation_table))
    except Exception as e:
        logger.warning("Could not load legacy allocation lookup table: %s", e)

    return allocation_table


def lookup_by_name(table: dict, payee_name: str) -> Optional:
    """
    Look up a value by name with exact then partial matching.

    Args:
        table: Dictionary with lowercase name keys
        payee_name: The payee/name from the transaction

    Returns:
        The matched value, or None if not found
    """
    if not payee_name:
        return None

    name_lower = payee_name.strip().lower()

    # Exact match first
    if name_lower in table:
        return table[name_lower]

    # Partial match
    for stored_name, value in table.items():
        if stored_name in name_lower or name_lower in stored_name:
            return value

    return None


def extract_exchange_rate_from_memo(entries: list) -> Optional[float]:
    """
    Extract exchange rate from entry memos.

    Patterns:
    - "Ex rate : 26.200" -> 26200 (period as thousands separator)
    - "exchange rate : 26,282" -> 26282
    - "1.1 USD x VND26250" -> 26250

    Returns:
        Exchange rate as float, or None if not found
    """
    for entry in entries:
        memo = entry.original_memo if hasattr(entry, "original_memo") else ""
        if not memo:
            continue

        rate = _parse_rate_from_text(memo)
        if rate:
            return rate

    return None


def _parse_rate_from_text(text: str) -> Optional[float]:
    """Parse exchange rate from a text string."""
    # Pattern 1: "rate" keyword followed by a number
    match = re.search(r"(?:rate)\s*[:\s]\s*([\d.,]+)", text, re.IGNORECASE)
    if match:
        rate = _normalize_rate_number(match.group(1))
        if rate and 10_000 <= rate <= 100_000:
            return rate

    # Pattern 2: "VND" followed by number (near multiplication context)
    match = re.search(r"x\s*VND\s*([\d.,]+)", text, re.IGNORECASE)
    if match:
        rate = _normalize_rate_number(match.group(1))
        if rate and 10_000 <= rate <= 100_000:
            return rate

    return None


def _normalize_rate_number(num_str: str) -> Optional[float]:
    """
    Normalize a rate number string by removing thousands separators.

    Handles both period and comma as thousands separators:
    - "26.200" -> 26200
    - "26,282" -> 26282
    - "26200" -> 26200
    """
    num_str = num_str.strip()

    # If the string has a period or comma followed by exactly 3 digits at the end,
    # treat it as a thousands separator
    cleaned = re.sub(r"[.,](?=\d{3}(?:\D|$))", "", num_str)
    cleaned = cleaned.rstrip(".,")

    try:
        return float(cleaned)
    except ValueError:
        return None
