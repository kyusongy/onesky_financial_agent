"""
Income section calculator.

Calculates:
- Contribution: deposit + name contains "onesky"
- Fund transfer to USD account: memo contains "transfer" AND NOT "ELC"
- Interest: deposit + memo contains "interest"
- PED: deposit + memo contains "PED"
"""
from ..models import TransactionGroup, BankType


def calculate_income(
    groups_by_bank: dict[BankType, list[TransactionGroup]]
) -> dict[BankType, dict[str, float]]:
    """
    Calculate income totals for each bank type.
    
    Args:
        groups_by_bank: Dictionary mapping BankType to list of transaction groups
        
    Returns:
        Dictionary mapping BankType to income category totals
    """
    result: dict[BankType, dict[str, float]] = {}
    
    for bank_type, groups in groups_by_bank.items():
        result[bank_type] = {
            "contribution": 0.0,
            "fund_transfer": 0.0,
            "interest": 0.0,
            "ped": 0.0,
        }
        
        for group in groups:
            contribution = _calculate_contribution(group)
            fund_transfer = _calculate_fund_transfer(group)
            interest = _calculate_interest(group)
            ped = _calculate_ped(group)
            
            result[bank_type]["contribution"] += contribution
            result[bank_type]["fund_transfer"] += fund_transfer
            result[bank_type]["interest"] += interest
            result[bank_type]["ped"] += ped
    
    return result


def _calculate_contribution(group: TransactionGroup) -> float:
    """
    Contribution: if transaction type is "deposit" AND name contains "onesky",
    sum up the amounts.
    """
    total = 0.0
    
    for row in group.rows:
        if row.is_deposit() and row.name_contains("onesky"):
            if row.amount is not None:
                total += row.amount
    
    return total


def _calculate_fund_transfer(group: TransactionGroup) -> float:
    """
    Fund transfer to USD account: if memo contains "transfer" AND NOT "ELC",
    sum up the amounts.
    """
    total = 0.0
    
    for row in group.rows:
        if row.memo_contains("transfer") and not row.memo_contains("elc"):
            if row.amount is not None:
                total += row.amount
    
    return total


def _calculate_interest(group: TransactionGroup) -> float:
    """
    Interest: if transaction type is "deposit" AND memo contains "interest",
    sum up the amounts.
    """
    total = 0.0
    
    for row in group.rows:
        if row.is_deposit() and row.memo_contains("interest"):
            if row.amount is not None:
                total += row.amount
    
    return total


def _calculate_ped(group: TransactionGroup) -> float:
    """
    PED: if transaction type is "deposit" AND memo contains "PED",
    sum up the amounts.
    """
    total = 0.0
    
    for row in group.rows:
        if row.is_deposit() and row.memo_contains("ped"):
            if row.amount is not None:
                total += row.amount
    
    return total

