"""
Advance/Settlement section calculator.

Calculates:
- Advance by cash: memo contains "advance" but NOT "settlement"
- Settlement: memo contains "settlement" AND bank_amount <= 0 (negative settlements only)

Note: Positive settlements go to Income section as "Cash settlement".
For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
from typing import Optional
from ..models import TransactionGroup, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount, init_advance_settlement_totals


def calculate_advance_settlement(
    groups_by_bank: dict[str, list[TransactionGroup]],
    exchange_rates: dict[str, float] = None,
    validation_data: Optional[ValidationData] = None
) -> dict[str, dict[str, float]]:
    """
    Calculate advance and settlement totals for each bank type.

    Args:
        groups_by_bank: Dictionary mapping bank_identifier to list of transaction groups
        exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
        validation_data: Optional ValidationData to track per-row contributions

    Returns:
        Dictionary mapping bank_identifier to advance/settlement totals (as positive values)
    """
    result: dict[str, dict[str, float]] = {}

    for bank_id, groups in groups_by_bank.items():
        result[bank_id] = init_advance_settlement_totals()

        for group in groups:
            ex_rate = get_exchange_rate(group, exchange_rates)

            advance = _calculate_advance(group, ex_rate)
            if advance != 0.0 and validation_data:
                # Advance uses abs - already converted for USD
                validation_data.set_value(group.original_row_index, "advance", advance)
            result[bank_id]["advance"] += advance

            settlement = _calculate_settlement(group, ex_rate)
            if settlement != 0.0 and validation_data:
                # Settlement preserves sign - already converted for USD
                validation_data.set_value(group.original_row_index, "settlement", settlement)
            result[bank_id]["settlement"] += settlement

    return result


def _calculate_advance(group: TransactionGroup, ex_rate: float) -> float:
    """Advance: header memo contains 'advance' but not 'settlement'."""
    if group.memo_contains("advance") and not group.memo_contains("settlement"):
        return abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
    return 0.0


def _calculate_settlement(group: TransactionGroup, ex_rate: float) -> float:
    """Settlement: header memo contains 'settlement' AND bank_amount <= 0. Use original bank_amount (preserve sign)."""
    if group.memo_contains("settlement") and group.bank_amount <= 0:
        return convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
    return 0.0
