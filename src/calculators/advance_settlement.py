"""
Advance/Settlement section calculator.

Calculates:
- Advance by cash: memo contains "advance" but NOT "advance settlement" or "settlement"
- Settlement: memo contains "settlement"

Note: Amounts from transactions are typically negative (money going out),
but in the report they should be shown as positive absolute values.

For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
from ..models import TransactionGroup, BankType


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
        result[bank_type] = {
            "advance": 0.0,
            "settlement": 0.0,
        }
        
        for group in groups:
            # Get exchange rate for USD conversion
            ex_rate = _get_exchange_rate(group, bank_type, exchange_rates)
            
            advance = _calculate_advance(group, ex_rate)
            settlement = _calculate_settlement(group, ex_rate)
            
            result[bank_type]["advance"] += advance
            result[bank_type]["settlement"] += settlement
    
    return result


def _get_exchange_rate(
    group: TransactionGroup, 
    bank_type: BankType, 
    exchange_rates: dict[str, float]
) -> float:
    """Get exchange rate for USD bank, return 1.0 for VND bank."""
    if bank_type != BankType.USD:
        return 1.0
    if not exchange_rates or not group.date:
        return 1.0
    date_key = group.date.strftime("%Y-%m-%d")
    return exchange_rates.get(date_key, 1.0)


def _convert_amount(amount: float, ex_rate: float) -> float:
    """Convert amount using exchange rate (divide for VND to USD)."""
    if ex_rate <= 0:
        return amount
    return amount / ex_rate


def _calculate_advance(group: TransactionGroup, ex_rate: float) -> float:
    """
    Advance by cash: if any memo contains "advance" but NOT "settlement",
    return the absolute value of first row's amount (converted if USD).
    """
    if group.any_memo_contains("advance") and not group.any_memo_contains("settlement"):
        # Return absolute value since advances are shown as positive in report
        return abs(_convert_amount(group.first_amount, ex_rate))
    return 0.0


def _calculate_settlement(group: TransactionGroup, ex_rate: float) -> float:
    """
    Settlement: if any memo contains "settlement",
    return the absolute value of first row's amount (converted if USD).
    """
    if group.any_memo_contains("settlement"):
        # Return absolute value since settlements are shown as positive in report
        return abs(_convert_amount(group.first_amount, ex_rate))
    return 0.0
