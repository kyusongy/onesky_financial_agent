"""
Income section calculator.

Calculates:
- Contribution: deposit + name contains "onesky"
- Fund transfer to USD account: memo contains "transfer" AND NOT "ELC"
- Interest: deposit + memo contains "interest"
- Cash settlement: memo contains "settlement" AND bank_amount > 0
- Fund transfer to ELC: memo contains "transfer to ELC"

Note: For grouped transactions, the transaction type (e.g., "deposit") is typically
on the header row, while the identifying memo/name may be on detail rows.
We check at the GROUP level for transaction type and look for keywords.

For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
from typing import Optional
from ..models import TransactionGroup, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount, init_income_totals


def calculate_income(
    groups_by_bank: dict[str, list[TransactionGroup]],
    exchange_rates: dict[str, float] = None,
    validation_data: Optional[ValidationData] = None
) -> dict[str, dict[str, float]]:
    """
    Calculate income totals for each bank type.

    Args:
        groups_by_bank: Dictionary mapping bank_identifier to list of transaction groups
        exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
        validation_data: Optional ValidationData to track per-row contributions

    Returns:
        Dictionary mapping bank_identifier to income category totals
    """
    result: dict[str, dict[str, float]] = {}

    for bank_id, groups in groups_by_bank.items():
        result[bank_id] = init_income_totals()

        for group in groups:
            ex_rate = get_exchange_rate(group, exchange_rates)

            # Calculate each income category and track validation data
            # For USD banks, use converted amount; for VND banks, use original amount
            contrib = _calculate_contribution(group, ex_rate)
            if contrib != 0.0 and validation_data:
                validation_data.set_value(group.original_row_index, "contribution", contrib)
            result[bank_id]["contribution"] += contrib

            fund_transfer = _calculate_fund_transfer(group, ex_rate)
            if fund_transfer != 0.0 and validation_data:
                validation_data.set_value(group.original_row_index, "fund_transfer", fund_transfer)
            result[bank_id]["fund_transfer"] += fund_transfer

            fund_transfer_elc = _calculate_fund_transfer_elc(group, ex_rate)
            if fund_transfer_elc != 0.0 and validation_data:
                validation_data.set_value(group.original_row_index, "fund_transfer_elc", fund_transfer_elc)
            result[bank_id]["fund_transfer_elc"] += fund_transfer_elc

            interest = _calculate_interest(group, ex_rate)
            if interest != 0.0 and validation_data:
                validation_data.set_value(group.original_row_index, "interest", interest)
            result[bank_id]["interest"] += interest

            cash_settlement = _calculate_cash_settlement(group, ex_rate)
            if cash_settlement != 0.0 and validation_data:
                validation_data.set_value(group.original_row_index, "cash_settlement", cash_settlement)
            result[bank_id]["cash_settlement"] += cash_settlement

    return result


def _calculate_contribution(group: TransactionGroup, ex_rate: float) -> float:
    """Contribution: deposit + name contains 'onesky'."""
    if group.is_deposit() and group.name_contains("onesky"):
        return convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
    return 0.0


def _calculate_fund_transfer(group: TransactionGroup, ex_rate: float) -> float:
    """Fund transfer: memo contains 'transfer' but not 'elc'."""
    if group.any_memo_contains("transfer") and not group.any_memo_contains("elc"):
        return convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
    return 0.0


def _calculate_fund_transfer_elc(group: TransactionGroup, ex_rate: float) -> float:
    """Fund transfer to ELC: memo contains 'transfer to ELC'."""
    if group.any_memo_contains("transfer") and group.any_memo_contains("elc"):
        return convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
    return 0.0


def _calculate_interest(group: TransactionGroup, ex_rate: float) -> float:
    """Interest: deposit + memo contains 'interest'."""
    if group.is_deposit() and group.any_memo_contains("interest"):
        return convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
    return 0.0


def _calculate_cash_settlement(group: TransactionGroup, ex_rate: float) -> float:
    """Cash settlement: memo contains 'settlement' AND bank_amount > 0."""
    if group.memo_contains("settlement") and group.bank_amount > 0:
        return convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
    return 0.0
