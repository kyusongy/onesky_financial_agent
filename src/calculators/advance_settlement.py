"""
Advance/Settlement section calculator.

Calculates:
- Advance by cash: memo contains "advance" but NOT "settlement"
- Settlement: memo contains "settlement"

Note: Amounts are shown as positive absolute values in the report.
For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
from ..models import TransactionGroup, BankType
from .utils import get_exchange_rate, convert_amount, init_advance_settlement_totals


def calculate_advance_settlement(
    groups_by_bank: dict[BankType, list[TransactionGroup]],
    exchange_rates: dict[str, float] = None
) -> dict[BankType, dict[str, float]]:
    """
    Calculate advance and settlement totals for each bank type.
    
    Args:
        groups_by_bank: Dictionary mapping BankType to list of transaction groups
        exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
        
    Returns:
        Dictionary mapping BankType to advance/settlement totals (as positive values)
    """
    result: dict[BankType, dict[str, float]] = {}
    
    for bank_type, groups in groups_by_bank.items():
        result[bank_type] = init_advance_settlement_totals()
        
        for group in groups:
            ex_rate = get_exchange_rate(group, exchange_rates)
            
            result[bank_type]["advance"] += _calculate_advance(group, ex_rate)
            result[bank_type]["settlement"] += _calculate_settlement(group, ex_rate)
    
    return result


def _calculate_advance(group: TransactionGroup, ex_rate: float) -> float:
    """Advance: memo contains 'advance' but not 'settlement'."""
    if group.any_memo_contains("advance") and not group.any_memo_contains("settlement"):
        return abs(convert_amount(group.first_amount, ex_rate))
    return 0.0


def _calculate_settlement(group: TransactionGroup, ex_rate: float) -> float:
    """Settlement: memo contains 'settlement'."""
    if group.any_memo_contains("settlement"):
        return abs(convert_amount(group.first_amount, ex_rate))
    return 0.0
