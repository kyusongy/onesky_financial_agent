"""
Advance/Settlement section calculator.

Calculates:
- Advance by cash: memo contains "advance" but NOT "settlement"
- Settlement: memo contains "settlement"

Note: Amounts are shown as positive absolute values in the report.
For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
from ..models import TransactionGroup, BANK_USD, BANK_VND
from .utils import get_exchange_rate, convert_amount, init_advance_settlement_totals


def calculate_advance_settlement(
    groups_by_bank: dict[str, list[TransactionGroup]],
    exchange_rates: dict[str, float] = None
) -> dict[str, dict[str, float]]:
    """
    Calculate advance and settlement totals for each bank type.
    
    Args:
        groups_by_bank: Dictionary mapping bank_identifier to list of transaction groups
        exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
        
    Returns:
        Dictionary mapping bank_identifier to advance/settlement totals (as positive values)
    """
    result: dict[str, dict[str, float]] = {}
    
    for bank_id, groups in groups_by_bank.items():
        result[bank_id] = init_advance_settlement_totals()
        
        for group in groups:
            ex_rate = get_exchange_rate(group, exchange_rates)
            
            result[bank_id]["advance"] += _calculate_advance(group, ex_rate)
            result[bank_id]["settlement"] += _calculate_settlement(group, ex_rate)
    
    return result


def _calculate_advance(group: TransactionGroup, ex_rate: float) -> float:
    """Advance: header memo contains 'advance' but not 'settlement'."""
    if group.memo_contains("advance") and not group.memo_contains("settlement"):
        return abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
    return 0.0


def _calculate_settlement(group: TransactionGroup, ex_rate: float) -> float:
    """Settlement: header memo contains 'settlement'."""
    if group.memo_contains("settlement"):
        return abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
    return 0.0
