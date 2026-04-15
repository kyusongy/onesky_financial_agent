"""
Province mapper for the "Expenditure by Province" section.

Processes transactions that have already passed through nature section,
distributing amounts to provinces based on:
1. Allocation lookup for salary/bonus transactions
2. Province code extraction from memo for standard transactions

Province section total should equal nature section total.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

from ..models import (
    TransactionGroup,
    TransactionEntry,
    ReportSection,
    BANK_USD,
    BANK_VND,
    BANK_34,
)
from ..validation import ValidationData
from .utils import (
    get_exchange_rate,
    convert_amount,
    load_staff_allocation_data,
    _load_allocation_legacy,
    lookup_by_name,
)
from config.mappings import (
    DEFAULT_STAFF_ALLOCATION_LOOKUP,
    DEFAULT_PROVINCE_PATTERNS,
    PROVINCE_CODES,
)
from ..lookup_parsers import parse_province_upload


def _is_1500(entry: TransactionEntry) -> bool:
    """Check if entry is a 1500 (payable) account."""
    return (entry.account_code or "").upper().startswith("1500")


def _calc_payable_contribution(
    payable_entries: list, bank_identifier: str, ex_rate: float
) -> float:
    """Calculate net payable contribution from 1500 entries (preserve sign)."""
    return sum(
        convert_amount(e.amount, bank_identifier, ex_rate)
        for e in payable_entries
        if e.amount
    )


class ProvinceMapper:
    """Maps transactions to province categories."""

    def __init__(
        self,
        allocation_data=None,
        patterns=None,
        allocation_source=None,
        patterns_source=None,
    ):
        """
        Initialize with lookup tables.

        Args:
            allocation_data: Pre-parsed {name: {province: pct}} dict. Skips file I/O when given.
            patterns: Pre-parsed list of (pattern_upper, code_lower), longest-first.
                     Skips file I/O when given.
            allocation_source: Path/file-like fallback for staff allocation when
                              allocation_data is None.
            patterns_source: Path/file-like fallback for province patterns when
                            patterns is None.
        """
        if allocation_data is not None:
            self.allocation_table = allocation_data
        else:
            src = allocation_source or DEFAULT_STAFF_ALLOCATION_LOOKUP
            _, self.allocation_table = load_staff_allocation_data(src)
            if not self.allocation_table:
                self.allocation_table = _load_allocation_legacy(src)

        if patterns is not None:
            self.patterns = patterns
        else:
            self.patterns = parse_province_upload(
                patterns_source or DEFAULT_PROVINCE_PATTERNS
            )

    def _extract_province_from_memo(self, memo: str) -> Optional[str]:
        """
        Extract province code from memo.

        Province codes may:
        - Appear before a number (e.g., "VNDN7100102" -> "vndn")
        - End with "OTO" suffix to be stripped (e.g., "VNDNOTO" -> "vndn")

        Returns:
            Normalized province code (lowercase) or None
        """
        if not memo:
            return None

        memo_upper = memo.upper()

        # self.patterns is already sorted longest-first by the parser
        for pattern_str, code in self.patterns:
            regex = rf"\b{re.escape(pattern_str)}(?:OTO)?(?:\d+)?"
            if re.search(regex, memo_upper):
                return code

        return None

    def _lookup_allocation(self, payee_name: str) -> Optional[dict[str, float]]:
        """Look up allocation percentages by payee name (exact then partial match)."""
        return lookup_by_name(self.allocation_table, payee_name)

    def _init_province_totals(self) -> dict[str, dict[str, float]]:
        """Initialize province totals dictionary for both banks."""
        return {
            BANK_USD: {code: 0.0 for code in PROVINCE_CODES},
            BANK_VND: {code: 0.0 for code in PROVINCE_CODES},
            BANK_34: {code: 0.0 for code in PROVINCE_CODES},
        }

    def process_groups(
        self,
        groups_by_bank: dict[str, list[TransactionGroup]],
        exchange_rates: dict[str, float] = None,
        validation_data: Optional[ValidationData] = None,
    ) -> dict[str, dict[str, float]]:
        """
        Process all groups and calculate province totals.

        Province section comes after nature section.
        Only processes groups that contributed to nature section
        (i.e., NOT income or advance/settlement).

        Args:
            groups_by_bank: Dictionary mapping bank_identifier to list of groups
            exchange_rates: Dictionary of date string to exchange rate
            validation_data: Optional ValidationData to track per-row contributions

        Returns:
            Province totals by bank
        """
        province_totals = self._init_province_totals()

        logger.debug("ProvinceMapper.process_groups")

        processed_count = 0

        for bank_id, groups in groups_by_bank.items():
            for group in groups:
                # Only process groups assigned to NATURE or MANUAL sections
                if group.assigned_section not in (
                    ReportSection.NATURE,
                    ReportSection.MANUAL,
                ):
                    continue

                # Skip groups that were processed as settlement/advance/payable (even if assigned to MANUAL)
                # These belong to Advance/Settlement/Payable or Income sections, not province
                active_entries = group.active_entries
                if active_entries and all(
                    e.nature_type
                    in ("settlement", "cash_settlement", "advance", "payable")
                    for e in active_entries
                ):
                    logger.debug(
                        "SKIP (settlement/advance/payable): %s", group.payee_name
                    )
                    continue

                ex_rate = get_exchange_rate(group, exchange_rates)

                # Process the group
                result = self._process_group(group, ex_rate, validation_data)

                # Accumulate totals
                for province, amount in result.items():
                    if province in province_totals[bank_id]:
                        province_totals[bank_id][province] += amount
                        if amount != 0:
                            logger.debug("ADD %s %s: %s", bank_id, province, amount)

                processed_count += 1

        logger.debug("ProvinceMapper SUMMARY: processed=%d", processed_count)
        logger.debug(
            "Province totals[VND]: %s",
            {k: v for k, v in province_totals[BANK_VND].items() if v != 0},
        )
        logger.debug(
            "Province totals[USD]: %s",
            {k: v for k, v in province_totals[BANK_USD].items() if v != 0},
        )

        return province_totals

    def _process_group(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None,
    ) -> dict[str, float]:
        """
        Process a single group for province mapping.

        Priority:
        1. Salary/Bonus: Use allocation lookup
        2. Standard: Extract province from memo
        """
        active_entries = group.active_entries
        num_entries = len(active_entries)

        bank_amount_converted = convert_amount(
            group.bank_amount, group.bank_identifier, ex_rate
        )
        expected_total = abs(bank_amount_converted)

        # Partition entries into payable (1500) and non-payable
        payable_entries = [e for e in active_entries if _is_1500(e)]
        non_payable_entries = [
            e
            for e in active_entries
            if not _is_1500(e)
            and e.nature_type not in ("advance", "settlement", "cash_settlement")
        ]

        non_payable_sum = sum(
            convert_amount(e.amount, group.bank_identifier, ex_rate)
            for e in non_payable_entries
            if e.amount
        )
        payable_contribution = _calc_payable_contribution(
            payable_entries, group.bank_identifier, ex_rate
        )
        base_amount = abs(non_payable_sum)

        # Dispatch to sub-method
        memo = (group.bank_memo or "").lower()
        is_salary_bonus = "salary" in memo or "bonus" in memo

        if is_salary_bonus:
            result = self._process_salary_province(
                group, base_amount, payable_contribution, expected_total
            )
        elif num_entries == 1:
            result = self._process_single_entry_province(
                group, active_entries[0], base_amount
            )
        elif num_entries > 1:
            result = self._process_multi_entry_province(
                group,
                ex_rate,
                non_payable_entries,
                payable_contribution,
                expected_total,
            )
        else:
            result = {}

        if validation_data and result:
            for province, amount in result.items():
                validation_data.set_value(group.original_row_index, province, amount)

        return result

    def _process_salary_province(
        self,
        group: TransactionGroup,
        base_amount: float,
        payable_contribution: float,
        expected_total: float,
    ) -> dict[str, float]:
        """Handle salary/bonus province allocation."""
        result: dict[str, float] = {}
        if base_amount == 0:
            return result

        allocations = self._lookup_allocation(group.payee_name)
        if allocations:
            for province, percentage in allocations.items():
                if percentage > 0:
                    result[province] = (
                        result.get(province, 0.0) + base_amount * percentage
                    )
            logger.debug(
                "SALARY/BONUS (allocation): %s -> %s", group.payee_name, result
            )
        else:
            province = self._extract_province_from_memo(group.bank_memo)
            if province:
                result[province] = base_amount
                logger.debug(
                    "SALARY/BONUS (memo fallback): %s -> %s: %s",
                    group.payee_name,
                    province,
                    base_amount,
                )
            else:
                result["province_manual"] = base_amount
                logger.debug(
                    "SALARY/BONUS (manual): %s -> province_manual: %s",
                    group.payee_name,
                    base_amount,
                )

        combined = sum(result.values()) + payable_contribution
        if abs(combined - expected_total) > 0.01:
            logger.warning(
                "Province sum mismatch! sum+payable=%s, expected=%s, diff=%s",
                combined,
                expected_total,
                combined - expected_total,
            )
        return result

    def _process_single_entry_province(
        self, group: TransactionGroup, entry: TransactionEntry, base_amount: float
    ) -> dict[str, float]:
        """Handle single-entry province assignment from memo."""
        if _is_1500(entry):
            return {}

        province = self._extract_province_from_memo(entry.original_memo)
        if not province:
            province = self._extract_province_from_memo(group.bank_memo)

        return {province or "province_manual": base_amount}

    def _process_multi_entry_province(
        self,
        group: TransactionGroup,
        ex_rate: float,
        non_payable_entries: list,
        payable_contribution: float,
        expected_total: float,
    ) -> dict[str, float]:
        """Handle multi-entry province assignment (each entry by memo)."""
        result: dict[str, float] = {}

        for entry in non_payable_entries:
            if not entry.amount:
                continue
            province = (
                self._extract_province_from_memo(entry.original_memo)
                or "province_manual"
            )
            amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
            result[province] = result.get(province, 0.0) + amount

        result_sum = sum(result.values()) + payable_contribution
        if abs(result_sum - expected_total) > 0.01:
            logger.warning(
                "Province sum mismatch! sum+payable=%s, expected=%s, diff=%s",
                result_sum,
                expected_total,
                result_sum - expected_total,
            )
        return result
