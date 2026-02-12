"""
Special account processor for 1230/1250/1252 (advance/settlement) and 1500 (payable).

Runs BEFORE NatureMapper. Marks processed entries as is_ignored so they're
excluded from active_entries in downstream processing.

Rules:
- 1230/1250/1252: positive amount -> advance, negative amount -> settlement
- 1500: payable (preserve original sign)
"""
import logging
from typing import Optional

from ..models import TransactionGroup, ReportSection
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount, get_account_type

logger = logging.getLogger(__name__)


def process_special_accounts(
    groups_by_bank: dict[str, list[TransactionGroup]],
    exchange_rates: dict[str, float] = None,
    validation_data: Optional[ValidationData] = None
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """
    Process 1230/1250/1252 entries as advance/settlement and 1500 entries as payable.

    Runs BEFORE NatureMapper. Marks processed entries as is_ignored so they're
    excluded from active_entries in downstream processing.

    Returns:
        Tuple of (advance_settlement totals, payable totals) by bank
    """
    adv_settle = {bank_id: {"advance": 0.0, "settlement": 0.0} for bank_id in groups_by_bank}
    payable = {bank_id: {"payable": 0.0} for bank_id in groups_by_bank}

    for bank_id, groups in groups_by_bank.items():
        for group in groups:
            if group.is_processed:
                continue

            ex_rate = get_exchange_rate(group, exchange_rates)
            special_amounts = {}

            for entry in list(group.active_entries):  # snapshot - we modify is_ignored
                acct_type = get_account_type(entry.account_code)
                if not acct_type:
                    continue

                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)

                if acct_type in ("1230", "1250", "1252"):
                    if entry.amount > 0:
                        entry.nature_type = "advance"
                        adv_settle[bank_id]["advance"] += amount
                        special_amounts["advance"] = special_amounts.get("advance", 0) + amount
                    else:
                        entry.nature_type = "settlement"
                        adv_settle[bank_id]["settlement"] += amount
                        special_amounts["settlement"] = special_amounts.get("settlement", 0) + amount
                    entry.is_ignored = True

                elif acct_type == "1500":
                    entry.nature_type = "payable"
                    payable[bank_id]["payable"] += amount
                    special_amounts["payable"] = special_amounts.get("payable", 0) + amount
                    entry.is_ignored = True

            # If ALL active entries were special -> mark group as processed
            if special_amounts and not group.active_entries:
                group.is_processed = True
                group.assigned_section = ReportSection.ADVANCE_SETTLEMENT

            # Record validation data
            if special_amounts and validation_data:
                for key, amt in special_amounts.items():
                    if amt != 0:
                        validation_data.set_value(group.original_row_index, key, amt)

    return adv_settle, payable
