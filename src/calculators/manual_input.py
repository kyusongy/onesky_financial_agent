"""
Manual input calculator for handling 1250/1230/1252/1500 accounts.

This module handles transactions flagged based on their account nature from Nature.xlsx:
- 1230VN → Advance if entry amount > 0, Settlement if entry amount < 0
- 1250VN → Settlement if entry memo contains "settlement", otherwise Advance
- 1252VN → Settlement if entry memo contains "settlement", otherwise Advance
- 1500VN → "payable" → Payable section

The simplified 1500 logic:
- Positive 1500 entries subtract from Payable only (not from other nature categories)
- Non-1500 entries in same group use Nature.xlsx lookup normally
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

from ..models import TransactionGroup, ReportSection, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import (
    get_exchange_rate, convert_amount, get_account_type,
    load_staff_allocation_data, lookup_by_name,
)
from config.mappings import DEFAULT_STAFF_ALLOCATION_LOOKUP


class ManualInputProcessor:
    """Processes manual input transactions using lookup tables."""

    def __init__(self, staff_allocation_source=None):
        """
        Initialize with staff & allocation lookup file.

        Args:
            staff_allocation_source: Path to Staff_&_Allocation.xlsx or file-like object.
                                    Uses default if not provided.
        """
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

        Args:
            manual_groups: List of transaction groups flagged for manual processing
            exchange_rates: Dictionary of date string to exchange rate
            validation_data: Optional ValidationData to track per-row contributions

        Returns:
            Tuple of (totals by bank, groups still needing manual review)
        """
        totals: dict[str, dict[str, float]] = {
            BANK_USD: self._init_totals(),
            BANK_VND: self._init_totals(),
        }
        still_manual: list[TransactionGroup] = []

        logger.debug("process_manual_groups: Total manual groups to process: %d", len(manual_groups))

        processed_count = 0
        skipped_already_processed = 0

        for i, group in enumerate(manual_groups):
            if group.is_processed:
                skipped_already_processed += 1
                logger.debug("[%d] SKIP (already processed): date=%s, section=%s", i, group.date, group.assigned_section)
                continue

            ex_rate = get_exchange_rate(group, exchange_rates)
            result = {"processed": False, "amounts": {}}

            # Priority 1: Check if memo contains "salary" or "bonus" - use staff lookup
            if self._is_salary_or_bonus_memo(group):
                result = self._process_salary(group, ex_rate, validation_data)
                if result["processed"]:
                    logger.debug("[%d] SALARY/BONUS: date=%s, name=%s, result=%s", i, group.date, group.payee_name, result['amounts'])
                else:
                    logger.debug("[%d] SALARY/BONUS memo but staff not found: name=%s", i, group.payee_name)

            # Priority 2: Process by account type based on nature from Nature.xlsx
            if not result["processed"]:
                result = self._process_by_account_nature(group, ex_rate, validation_data)
                if result["processed"]:
                    logger.debug("[%d] ACCOUNT NATURE: date=%s, result=%s", i, group.date, result['amounts'])

            if result["processed"]:
                group.is_processed = True
                group.assigned_section = ReportSection.MANUAL
                processed_count += 1
                if validation_data:
                    for key, amount in result["amounts"].items():
                        if amount != 0:
                            validation_data.set_value(group.original_row_index, key, amount)
                for key, amount in result["amounts"].items():
                    if key in totals[group.bank_identifier]:
                        old_val = totals[group.bank_identifier][key]
                        totals[group.bank_identifier][key] += amount
                        logger.debug("[%d] ADD %s: %s + %s = %s", i, key, old_val, amount, totals[group.bank_identifier][key])
            else:
                still_manual.append(group)

        logger.debug("process_manual_groups SUMMARY: total=%d, skipped=%d, processed=%d, still_manual=%d",
                      len(manual_groups), skipped_already_processed, processed_count, len(still_manual))
        logger.debug("Final totals[VND]: %s", {k: v for k, v in totals[BANK_VND].items() if v != 0})
        logger.debug("Final totals[USD]: %s", {k: v for k, v in totals[BANK_USD].items() if v != 0})

        return totals, still_manual

    def _init_totals(self) -> dict[str, float]:
        """Initialize totals dictionary with all categories."""
        return {
            "org": 0.0, "edu": 0.0, "oper": 0.0,
            "nutrition": 0.0, "edu_infra": 0.0,
            "advance": 0.0, "settlement": 0.0,
            "cash_settlement": 0.0,
            "payable": 0.0,  # NEW: For 1500 accounts
        }

    def _is_salary_or_bonus_memo(self, group: TransactionGroup) -> bool:
        """Check if header memo contains 'salary' or 'bonus'."""
        memo = group.bank_memo.lower() if group.bank_memo else ""
        return "salary" in memo or "bonus" in memo

    def _process_salary(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None
    ) -> dict:
        """
        Process salary/bonus transactions by looking up staff name.

        If header memo contains "salary" or "bonus", look up payee_name in staff lookup
        to determine nature (org/edu).

        Three cases:
        1. All entries are 1500 → skip salary, fall through to payable processing
        2. Has non-1500 entries but staff not found → fall through to account type processing
        3. Staff found + has non-1500 entries → split: 1500→payable, rest→org/edu
        """
        result = {"processed": False, "amounts": {}}

        active_entries = group.active_entries
        payable_entries = [e for e in active_entries if get_account_type(e.account_code) == "1500"]
        non_payable_entries = [e for e in active_entries if get_account_type(e.account_code) != "1500"]

        # Case 1: ALL entries are 1500 → pure payable, skip salary processing entirely
        if not non_payable_entries:
            logger.debug("SALARY/BONUS: all entries are 1500, skipping to payable processing")
            return result  # Falls through to _process_by_account_nature → payable

        # Case 2 & 3: Has non-1500 entries → try staff lookup
        nature = self.lookup_staff_by_name(group.payee_name)

        if not nature:
            # Case 2: Staff not found → fall through to _process_by_account_nature
            return result

        # Case 3: Staff found + has non-1500 entries → split payable/salary
        bank_amount_converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
        non_payable_sum = sum(
            convert_amount(e.amount, group.bank_identifier, ex_rate)
            for e in non_payable_entries
            if e.amount
        )

        # Process 1500 entries as payable
        net_payable = 0.0
        for entry in payable_entries:
            if entry.amount:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                if entry.amount < 0:
                    contribution = abs(amount)
                    net_payable += contribution
                    result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) + contribution
                    logger.debug("SALARY/1500 (negative): payable += %s", contribution)
                else:
                    net_payable -= amount
                    result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) - amount
                    logger.debug("SALARY/1500 (positive): payable -= %s", amount)

        # Salary = sum of non-1500 entry amounts
        salary_amount = non_payable_sum
        result["amounts"][nature] = salary_amount
        result["processed"] = True

        expected = -bank_amount_converted
        combined = non_payable_sum + net_payable
        if abs(combined - expected) > 0.01:
            logger.warning("Manual salary sum mismatch! sum+payable=%s, expected=%s, diff=%s", combined, expected, combined - expected)

        logger.debug("SALARY split: %s=%s, payable=%s", nature, salary_amount, result['amounts'].get('payable', 0.0))

        # Only overwrite nature_type on non-1500 entries
        for entry in group.entries:
            if get_account_type(entry.account_code) != "1500":
                entry.nature_type = nature

        return result

    def _process_by_account_nature(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None
    ) -> dict:
        """
        Process group based on account nature from Nature.xlsx.

        - 1230VN -> Advance if entry amount > 0, Settlement if entry amount < 0
        - 1250VN -> Settlement if entry memo contains "settlement", otherwise Advance
        - 1252VN -> Settlement if entry memo contains "settlement", otherwise Advance
        - 1500VN -> Payable section (positive 1500 subtracts from payable)
        """
        active_entries = group.active_entries
        bank_amount_converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)

        logger.debug("_process_by_account_nature: date=%s, bank=%s, amount=%s, converted=%s, entries=%d",
                      group.date, group.bank_identifier, group.bank_amount, bank_amount_converted, len(active_entries))

        categorized = self._categorize_entries(active_entries)

        num_entries = len(active_entries)
        if num_entries == 1:
            result = self._process_single_entry_account(active_entries[0], bank_amount_converted)
        elif num_entries > 1:
            result = self._process_multi_entry_account(
                group, ex_rate, bank_amount_converted, active_entries, categorized
            )
        else:
            result = {"processed": False, "amounts": {}}

        logger.debug("Final result: %s", result)
        return result

    def _categorize_entries(self, active_entries: list) -> dict[str, list]:
        """Categorize entries by account type, assigning nature_type as a side effect."""
        categorized = {"advance": [], "settlement": [], "payable": [], "other": []}

        for entry in active_entries:
            acct_type = get_account_type(entry.account_code)
            entry_memo = (entry.original_memo or "").lower()

            if acct_type == "1230":
                if entry.amount and entry.amount > 0:
                    categorized["advance"].append(entry)
                    entry.nature_type = "advance"
                    logger.debug("Entry 1230: amount=%s > 0 -> advance", entry.amount)
                else:
                    categorized["settlement"].append(entry)
                    entry.nature_type = "settlement"
                    logger.debug("Entry 1230: amount=%s <= 0 -> settlement", entry.amount)
            elif acct_type in ("1250", "1252"):
                if "settlement" in entry_memo:
                    categorized["settlement"].append(entry)
                    entry.nature_type = "settlement"
                    logger.debug("Entry %s: memo contains 'settlement' -> settlement", acct_type)
                else:
                    categorized["advance"].append(entry)
                    entry.nature_type = "advance"
                    logger.debug("Entry %s: memo='%s' -> advance", acct_type, entry_memo)
            elif acct_type == "1500":
                categorized["payable"].append(entry)
                entry.nature_type = "payable"
            else:
                categorized["other"].append(entry)

            logger.debug("Entry: account=%s, amount=%s, nature=%s", entry.account_code, entry.amount, entry.nature_type)

        return categorized

    def _process_single_entry_account(self, entry, bank_amount_converted: float) -> dict:
        """Handle single-entry groups: use abs(bank_amount) under the entry's nature."""
        result = {"processed": False, "amounts": {}}
        nature = entry.nature_type

        if nature:
            converted = abs(bank_amount_converted)
            result["amounts"][nature] = converted
            result["processed"] = True
            logger.debug("SINGLE %s: %s", nature.upper(), converted)

        return result

    def _process_multi_entry_account(
        self, group: TransactionGroup, ex_rate: float,
        bank_amount_converted: float, active_entries: list, categorized: dict[str, list]
    ) -> dict:
        """Handle multi-entry groups: process each category separately."""
        result = {"processed": True, "amounts": {}}

        # Process other entries (non-special accounts) - use their nature from NatureMapper
        for entry in categorized["other"]:
            if entry.amount and entry.nature_type:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                result["amounts"][entry.nature_type] = result["amounts"].get(entry.nature_type, 0.0) + amount
                logger.debug("OTHER entry: %s += %s", entry.nature_type, amount)

        # Process advance entries
        for entry in categorized["advance"]:
            if entry.amount:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                result["amounts"]["advance"] = result["amounts"].get("advance", 0.0) + abs(amount)
                logger.debug("ADVANCE entry: advance += abs(%s)", amount)

        # Process settlement entries
        for entry in categorized["settlement"]:
            if entry.amount:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                result["amounts"]["settlement"] = result["amounts"].get("settlement", 0.0) + abs(amount)
                logger.debug("SETTLEMENT entry: settlement += abs(%s)", amount)

        # Process payable entries (1500) - negative adds, positive subtracts
        net_payable = 0.0
        for entry in categorized["payable"]:
            if entry.amount:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                if entry.amount < 0:
                    result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) + abs(amount)
                    net_payable += abs(amount)
                    logger.debug("PAYABLE (negative) entry: payable += abs(%s)", amount)
                else:
                    result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) - amount
                    net_payable -= amount
                    logger.debug("PAYABLE (positive) entry: payable -= %s", amount)

        # Validation check
        if categorized["payable"] and any(get_account_type(e.account_code) != "1500" for e in active_entries):
            non_payable_sum = sum(
                convert_amount(e.amount, group.bank_identifier, ex_rate)
                for e in active_entries
                if e.amount and get_account_type(e.account_code) != "1500"
            )
            expected = -bank_amount_converted
            combined = non_payable_sum + net_payable
            if abs(combined - expected) > 0.01:
                logger.warning("Manual sum mismatch! sum+payable=%s, expected=%s, diff=%s", combined, expected, combined - expected)

        return result


def mark_processed_groups(
    groups_by_bank: dict[str, list[TransactionGroup]],
    income: dict[str, dict[str, float]],
    advance_settlement: dict[str, dict[str, float]],
    nature_totals: dict[str, dict[str, float]]
) -> None:
    """
    Mark groups as processed based on what sections they contributed to.
    Helps avoid double counting when processing manual input.
    """
    for groups in groups_by_bank.values():
        for group in groups:
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

    # Advance/Settlement checks - ONLY check header memo (bank_memo), not entry memos
    if group.memo_contains("settlement") and group.bank_amount <= 0:
        return ReportSection.ADVANCE_SETTLEMENT
    if group.memo_contains("advance"):
        return ReportSection.ADVANCE_SETTLEMENT

    # Check if any entry is a manual trigger account (1500/1250/1230/1252)
    # If so, do NOT mark as NATURE - let manual_input.py handle the complex logic
    active_entries = group.active_entries
    if any(e.is_manual_trigger for e in active_entries):
        return None  # Leave for manual processing

    # Nature check (non-manual) - only if no manual trigger entries
    if any(e.nature_type and e.nature_type not in ("manual", "advance", "settlement", "payable") for e in active_entries):
        return ReportSection.NATURE

    return None
