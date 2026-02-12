"""
Manual input calculator for salary/bonus transactions.

After special accounts (1230/1250/1252/1500) are processed upstream,
this module handles:
- Salary/bonus transactions via staff name lookup
- Groups that couldn't be auto-categorized (flagged for manual review)
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from ..models import TransactionGroup, ReportSection, BANK_USD, BANK_VND, BANK_34
from ..validation import ValidationData
from .utils import (
    get_exchange_rate, convert_amount,
    load_staff_allocation_data, lookup_by_name,
)
from config.mappings import DEFAULT_STAFF_ALLOCATION_LOOKUP


class ManualInputProcessor:
    """Processes manual input transactions using lookup tables."""

    def __init__(self, staff_allocation_source=None):
        source = staff_allocation_source or DEFAULT_STAFF_ALLOCATION_LOOKUP
        self.staff_lookup, self.allocation_lookup = load_staff_allocation_data(source)

    def lookup_staff_by_name(self, payee_name: str) -> Optional[str]:
        """Look up nature by staff name (exact then partial match)."""
        return lookup_by_name(self.staff_lookup, payee_name)

    def lookup_allocation_by_name(self, payee_name: str) -> Optional[dict[str, float]]:
        """Look up allocation percentages by staff name (exact then partial match)."""
        return lookup_by_name(self.allocation_lookup, payee_name)

    def process_manual_groups(
        self,
        manual_groups: list[TransactionGroup],
        exchange_rates: dict[str, float] = None,
        validation_data: Optional[ValidationData] = None
    ) -> tuple[dict[str, dict[str, float]], list[TransactionGroup]]:
        """
        Process manual input groups and calculate totals.

        Returns:
            Tuple of (totals by bank, groups still needing manual review)
        """
        totals: dict[str, dict[str, float]] = {
            BANK_USD: self._init_totals(),
            BANK_VND: self._init_totals(),
            BANK_34: self._init_totals(),
        }
        still_manual: list[TransactionGroup] = []

        for group in manual_groups:
            if group.is_processed:
                continue

            ex_rate = get_exchange_rate(group, exchange_rates)
            result = {"processed": False, "amounts": {}}

            if self._is_salary_or_bonus_memo(group):
                result = self._process_salary(group, ex_rate)

            if not result["processed"]:
                still_manual.append(group)
                continue

            group.is_processed = True
            group.assigned_section = ReportSection.MANUAL
            if validation_data:
                for key, amount in result["amounts"].items():
                    if amount != 0:
                        validation_data.set_value(group.original_row_index, key, amount)
            for key, amount in result["amounts"].items():
                if key in totals[group.bank_identifier]:
                    totals[group.bank_identifier][key] += amount

        return totals, still_manual

    def _init_totals(self) -> dict[str, float]:
        """Initialize totals dictionary with nature categories only."""
        return {"org": 0.0, "edu": 0.0, "oper": 0.0, "nutrition": 0.0, "edu_infra": 0.0}

    def _is_salary_or_bonus_memo(self, group: TransactionGroup) -> bool:
        """Check if header memo contains 'salary' or 'bonus'."""
        memo = group.bank_memo.lower() if group.bank_memo else ""
        return "salary" in memo or "bonus" in memo

    def _process_salary(
        self,
        group: TransactionGroup,
        ex_rate: float,
    ) -> dict:
        """
        Process salary/bonus transactions by looking up staff name.

        Special account entries are already is_ignored, so active_entries
        only contains non-special entries.
        """
        result = {"processed": False, "amounts": {}}
        active = group.active_entries

        if not active:
            # All entries were special accounts (already processed upstream)
            result["processed"] = True
            return result

        nature = self.lookup_staff_by_name(group.payee_name)

        if not nature:
            # Staff not found - use entry natures from Nature.xlsx
            for entry in active:
                if entry.nature_type and entry.amount:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    result["amounts"][entry.nature_type] = result["amounts"].get(entry.nature_type, 0) + amount
            result["processed"] = True
            return result

        # Staff found - assign staff's nature to all remaining entries
        for entry in active:
            entry.nature_type = nature
            if entry.amount:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                result["amounts"][nature] = result["amounts"].get(nature, 0) + amount
        result["processed"] = True
        return result


def mark_processed_groups(
    groups_by_bank: dict[str, list[TransactionGroup]],
) -> None:
    """
    Mark groups as processed based on what sections they contributed to.
    Helps avoid double counting when processing manual input.
    """
    for groups in groups_by_bank.values():
        for group in groups:
            if group.is_processed:
                continue  # Already processed (e.g., by process_special_accounts)
            section = _get_processed_section(group)
            group.is_processed = section is not None
            group.assigned_section = section


def _get_processed_section(group: TransactionGroup) -> Optional[ReportSection]:
    """Determine which section processed this group."""
    # Salary/bonus groups should be handled by ManualInputProcessor
    if group.any_memo_contains("salary") or group.any_memo_contains("bonus"):
        return None

    # Income checks
    if group.is_deposit():
        if group.name_contains("onesky"):
            return ReportSection.INCOME
        if group.any_memo_contains("interest"):
            return ReportSection.INCOME

    if group.any_memo_contains("transfer"):
        return ReportSection.INCOME

    # Cash settlement (positive settlement) goes to Income
    if group.memo_contains("settlement") and group.bank_amount > 0:
        return ReportSection.INCOME

    # Check if any entry has "manual" nature trigger
    active_entries = group.active_entries
    if any(e.is_manual_trigger for e in active_entries):
        return None  # Leave for manual processing

    # Nature check
    if any(e.nature_type for e in active_entries):
        return ReportSection.NATURE

    return None
