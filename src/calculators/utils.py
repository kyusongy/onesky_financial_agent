"""
Shared utility functions for calculators.

Consolidates common operations like exchange rate handling and amount conversion
to eliminate code duplication across calculator modules.
"""
from typing import Optional
from datetime import datetime

from ..models import TransactionGroup, BANK_USD, BANK_VND


def get_exchange_rate(
    group: TransactionGroup,
    exchange_rates: Optional[dict[str, float]] = None
) -> float:
    """
    Get exchange rate for a transaction group.
    
    Returns 1.0 for VND bank (no conversion needed).
    For USD bank, looks up rate by date.
    
    Args:
        group: Transaction group
        exchange_rates: Dictionary of date string to exchange rate
        
    Returns:
        Exchange rate (1.0 if not applicable or not found)
    """
    if group.bank_identifier != BANK_USD:
        return 1.0
    if not exchange_rates or not group.date:
        return 1.0
    date_key = group.date.strftime("%Y-%m-%d")
    return exchange_rates.get(date_key, 1.0)


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


def is_manual_account(account_number: Optional[str]) -> bool:
    """Check if account number triggers manual input processing."""
    return get_account_type(account_number) is not None


# Nature normalization mapping
NATURE_NORMALIZATION_MAP = {
    "advance": "advance",
    "settlement": "settlement",
    "org": "org",
    "edu": "edu",
    "oper": "oper",
    "nutrition": "nutrition",
    "infra": "edu_infra",
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
