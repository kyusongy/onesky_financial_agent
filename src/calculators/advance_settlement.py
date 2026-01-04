"""
Advance/Settlement section calculator.

Calculates:
- Advance by cash: memo contains "advance" but NOT "advance settlement"
- Settlement: memo contains "settlement"
"""
from ..models import TransactionGroup, BankType


def calculate_advance_settlement(
    groups_by_bank: dict[BankType, list[TransactionGroup]]
) -> dict[BankType, dict[str, float]]:
    """
    Calculate advance and settlement totals for each bank type.
    
    Args:
        groups_by_bank: Dictionary mapping BankType to list of transaction groups
        
    Returns:
        Dictionary mapping BankType to advance/settlement totals
    """
    result: dict[BankType, dict[str, float]] = {}
    
    for bank_type, groups in groups_by_bank.items():
        result[bank_type] = {
            "advance": 0.0,
            "settlement": 0.0,
        }
        
        for group in groups:
            advance = _calculate_advance(group)
            settlement = _calculate_settlement(group)
            
            result[bank_type]["advance"] += advance
            result[bank_type]["settlement"] += settlement
    
    return result


def _calculate_advance(group: TransactionGroup) -> float:
    """
    Advance by cash: if memo contains "advance" but NOT "advance settlement",
    sum up the amounts.
    """
    total = 0.0
    
    for row in group.rows:
        if row.memo_contains("advance") and not row.memo_contains("advance settlement"):
            # Also exclude if it just says "settlement" somewhere
            if not row.memo_contains("settlement"):
                if row.amount is not None:
                    total += row.amount
    
    return total


def _calculate_settlement(group: TransactionGroup) -> float:
    """
    Settlement: if memo contains "settlement", sum up the amounts.
    """
    total = 0.0
    
    for row in group.rows:
        if row.memo_contains("settlement"):
            if row.amount is not None:
                total += row.amount
    
    return total

