"""
Income section calculator.

Calculates:
- Contribution: deposit + name contains "onesky"
- Fund transfer to USD account: memo contains "transfer" AND NOT "ELC"
- Interest: deposit + memo contains "interest"
- PED: deposit + memo contains "PED"

Note: For grouped transactions, the transaction type (e.g., "deposit") is typically
on the first row, while the identifying memo/name may be on detail rows.
We check at the GROUP level for transaction type and look for keywords in ANY row.

For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
from ..models import TransactionGroup, BankType
from .utils import get_exchange_rate, convert_amount, init_income_totals


def calculate_income(
    groups_by_bank: dict[BankType, list[TransactionGroup]],
    exchange_rates: dict[str, float] = None
) -> dict[BankType, dict[str, float]]:
    """
    Calculate income totals for each bank type.
    
    Args:
        groups_by_bank: Dictionary mapping BankType to list of transaction groups
        exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
        
    Returns:
        Dictionary mapping BankType to income category totals
    """
    result: dict[BankType, dict[str, float]] = {}
    
    for bank_type, groups in groups_by_bank.items():
        result[bank_type] = init_income_totals()
        
        for group in groups:
            ex_rate = get_exchange_rate(group, exchange_rates)
            
            result[bank_type]["contribution"] += _calculate_contribution(group, ex_rate)
            result[bank_type]["fund_transfer"] += _calculate_fund_transfer(group, ex_rate)
            result[bank_type]["interest"] += _calculate_interest(group, ex_rate)
            result[bank_type]["ped"] += _calculate_ped(group, ex_rate)
    
    return result


def _calculate_contribution(group: TransactionGroup, ex_rate: float) -> float:
    """Contribution: deposit + name contains 'onesky'."""
    if group.has_deposit_type() and group.any_name_contains("onesky"):
        return convert_amount(group.first_amount, ex_rate)
    return 0.0


def _calculate_fund_transfer(group: TransactionGroup, ex_rate: float) -> float:
    """Fund transfer: memo contains 'transfer' but not 'elc'."""
    if group.any_memo_contains("transfer") and not group.any_memo_contains("elc"):
        return convert_amount(group.first_amount, ex_rate)
    return 0.0


def _calculate_interest(group: TransactionGroup, ex_rate: float) -> float:
    """Interest: deposit + memo contains 'interest'."""
    if group.has_deposit_type() and group.any_memo_contains("interest"):
        return convert_amount(group.first_amount, ex_rate)
    return 0.0


def _calculate_ped(group: TransactionGroup, ex_rate: float) -> float:
    """PED: deposit + memo contains 'ped'."""
    if group.has_deposit_type() and group.any_memo_contains("ped"):
        return convert_amount(group.first_amount, ex_rate)
    return 0.0
