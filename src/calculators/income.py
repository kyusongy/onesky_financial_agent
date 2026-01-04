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
        result[bank_type] = {
            "contribution": 0.0,
            "fund_transfer": 0.0,
            "interest": 0.0,
            "ped": 0.0,
        }
        
        for group in groups:
            # Get exchange rate for USD conversion
            ex_rate = _get_exchange_rate(group, bank_type, exchange_rates)
            
            contribution = _calculate_contribution(group, ex_rate)
            fund_transfer = _calculate_fund_transfer(group, ex_rate)
            interest = _calculate_interest(group, ex_rate)
            ped = _calculate_ped(group, ex_rate)
            
            result[bank_type]["contribution"] += contribution
            result[bank_type]["fund_transfer"] += fund_transfer
            result[bank_type]["interest"] += interest
            result[bank_type]["ped"] += ped
    
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


def _calculate_contribution(group: TransactionGroup, ex_rate: float) -> float:
    """
    Contribution: if group has deposit type AND any name contains "onesky",
    return the first row's amount (converted if USD).
    """
    if group.has_deposit_type() and group.any_name_contains("onesky"):
        return _convert_amount(group.first_amount, ex_rate)
    return 0.0


def _calculate_fund_transfer(group: TransactionGroup, ex_rate: float) -> float:
    """
    Fund transfer to USD account: if any memo contains "transfer" AND NOT "ELC",
    return the first row's amount (converted if USD).
    """
    if group.any_memo_contains("transfer") and not group.any_memo_contains("elc"):
        return _convert_amount(group.first_amount, ex_rate)
    return 0.0


def _calculate_interest(group: TransactionGroup, ex_rate: float) -> float:
    """
    Interest: if group has deposit type AND any memo contains "interest",
    return the first row's amount (converted if USD).
    """
    if group.has_deposit_type() and group.any_memo_contains("interest"):
        return _convert_amount(group.first_amount, ex_rate)
    return 0.0


def _calculate_ped(group: TransactionGroup, ex_rate: float) -> float:
    """
    PED: if group has deposit type AND any memo contains "PED",
    return the first row's amount (converted if USD).
    """
    if group.has_deposit_type() and group.any_memo_contains("ped"):
        return _convert_amount(group.first_amount, ex_rate)
    return 0.0
