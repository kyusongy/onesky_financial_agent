"""
Nature mapper for the "by nature" section.

Maps transactions to nature categories using the lookup table.
Applies filling logic based on number of rows and nature types.

Note: Expense amounts are shown as positive values in the report.
For USD bank, amounts are converted from VND to USD using the exchange rate.
"""

import logging
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union

logger = logging.getLogger(__name__)

from ..models import TransactionGroup, TransactionEntry, BANK_USD, BANK_VND, BANK_34
from ..validation import ValidationData
from .utils import (
    get_exchange_rate,
    convert_amount,
    init_nature_totals,
    get_account_type,
)
from config.mappings import DEFAULT_NATURE_LOOKUP, NATURE_CATEGORY_MAP


class NatureMapper:
    """Maps transactions to nature categories."""

    def __init__(
        self,
        lookup: Optional[dict[str, str]] = None,
        source: Optional[Union[str, Path, BinaryIO]] = None,
    ):
        """
        Initialize with nature lookup table.

        Args:
            lookup: Pre-parsed canonical mapping (account_lower -> nature_lower).
                   When provided, skips all file I/O.
            source: Path to lookup file or file-like object. Used only when
                   `lookup` is None. Falls back to DEFAULT_NATURE_LOOKUP.
        """
        if lookup is not None:
            self.lookup_table = lookup
        else:
            self.lookup_table = self._load_lookup_table(source)

    def _load_lookup_table(
        self, source: Optional[Union[str, Path, BinaryIO]]
    ) -> dict[str, str]:
        """
        Load and parse the nature lookup table.

        For defaults format (nature.xlsx):
        - Sheet: "Lookup1_AcctList"
        - Column A (0): Account No.
        - Column B (1): PACCOM nature
        """
        if source is None:
            source = DEFAULT_NATURE_LOOKUP

        # Try to load with defaults format first
        try:
            df = pd.read_excel(
                source, sheet_name="Lookup1_AcctList", header=None, engine="openpyxl"
            )
            logger.debug("Loaded nature.xlsx (defaults format)")

            # Find header row (contains "Account No.")
            header_idx = 0
            for idx in range(min(10, len(df))):
                row = df.iloc[idx]
                for val in row.values:
                    if isinstance(val, str) and "account no" in val.lower():
                        header_idx = idx
                        break

            # Parse: Column A = Account No., Column B = Nature
            lookup: dict[str, str] = {}
            for idx in range(header_idx + 1, len(df)):
                row = df.iloc[idx]
                account_no = row.iloc[0] if len(row) > 0 else None
                nature = row.iloc[1] if len(row) > 1 else None

                if pd.notna(account_no) and pd.notna(nature):
                    account_key = str(account_no).strip().lower()
                    nature_value = str(nature).strip().lower()
                    lookup[account_key] = nature_value

            logger.info("Loaded %d account mappings", len(lookup))
            return lookup

        except Exception as e:
            logger.warning("Could not load defaults format: %s", e)
            logger.info("Falling back to legacy format...")

        # Fallback to legacy format (old nature_lookup.xlsx)
        df = pd.read_excel(source, header=None, engine="openpyxl")

        # Find the header row (contains "Account No.")
        header_idx = 3  # Default
        for idx in range(min(10, len(df))):
            row = df.iloc[idx]
            for val in row.values:
                if isinstance(val, str) and "account no" in val.lower():
                    header_idx = idx
                    break

        # Parse the lookup table
        # Column 1: Account No., Column 7: Nature category
        lookup: dict[str, str] = {}

        for idx in range(header_idx + 1, len(df)):
            row = df.iloc[idx]
            account_no = row.iloc[1] if len(row) > 1 else None
            nature = row.iloc[7] if len(row) > 7 else None

            if pd.notna(account_no) and pd.notna(nature):
                account_key = str(account_no).strip().lower()
                nature_value = str(nature).strip().lower()
                lookup[account_key] = nature_value

        return lookup

    def get_nature(self, account_number: Optional[str]) -> Optional[str]:
        """
        Get the nature category for an account number.

        Args:
            account_number: Account number (e.g., "71101VN")

        Returns:
            Normalized nature key (e.g., "org", "edu", "manual") or None
        """
        if not account_number:
            return None

        account_key = account_number.strip().lower()
        nature_raw = self.lookup_table.get(account_key)

        if not nature_raw:
            return None

        # Map to normalized category key
        for pattern, category in NATURE_CATEGORY_MAP.items():
            if pattern in nature_raw:
                return category

        return None

    def get_nature_display(self, account_number: Optional[str]) -> Optional[str]:
        """
        Get the raw nature category string for display purposes.

        Args:
            account_number: Account number (e.g., "71101VN")

        Returns:
            Raw nature string from lookup table or None
        """
        if not account_number:
            return None

        account_key = account_number.strip().lower()
        return self.lookup_table.get(account_key)

    def process_groups(
        self,
        groups_by_bank: dict[str, list[TransactionGroup]],
        exchange_rates: dict[str, float] = None,
        validation_data: Optional[ValidationData] = None,
    ) -> tuple[dict[str, dict[str, float]], list[TransactionGroup]]:
        """
        Process all groups and calculate nature totals.

        Args:
            groups_by_bank: Dictionary mapping bank_identifier to list of groups
            exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
            validation_data: Optional ValidationData to track per-row contributions

        Returns:
            Tuple of (nature_totals by bank, list of manual review groups)
        """
        nature_totals: dict[str, dict[str, float]] = {
            BANK_USD: init_nature_totals(),
            BANK_VND: init_nature_totals(),
            BANK_34: init_nature_totals(),
        }
        manual_groups: list[TransactionGroup] = []

        for bank_id, groups in groups_by_bank.items():
            for group in groups:
                ex_rate = get_exchange_rate(group, exchange_rates)
                result = self._process_group(group, ex_rate, validation_data)

                if result["is_manual"]:
                    manual_groups.append(group)
                    nature_totals[bank_id]["manual"] += abs(result["manual_amount"])
                else:
                    for nature_key, amount in result["nature_amounts"].items():
                        if nature_key in nature_totals[bank_id]:
                            nature_totals[bank_id][nature_key] += amount

        return nature_totals, manual_groups

    def _process_group(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None,
    ) -> dict:
        """
        Process a single group for nature mapping.

        Returns:
            Dict with keys: is_manual, manual_amount, nature_amounts
        """
        result = {"is_manual": False, "manual_amount": 0.0, "nature_amounts": {}}

        active_entries = group.active_entries
        if not active_entries:
            return result

        bank_amount_converted = convert_amount(
            group.bank_amount, group.bank_identifier, ex_rate
        )
        logger.debug(
            "_process_group (NatureMapper): date=%s, bank=%s, amount=%s, converted=%s, entries=%d",
            group.date,
            group.bank_identifier,
            group.bank_amount,
            bank_amount_converted,
            len(active_entries),
        )

        self._assign_entry_natures(active_entries)

        deferral = self._check_deferral(group, active_entries, bank_amount_converted)
        if deferral:
            return deferral

        result["nature_amounts"] = self._calculate_nature_amounts(
            group, active_entries, ex_rate, bank_amount_converted
        )

        if validation_data and result["nature_amounts"]:
            for nature_key, amount in result["nature_amounts"].items():
                validation_data.set_value(group.original_row_index, nature_key, amount)

        logger.debug("  Final result: %s", result)
        return result

    def _assign_entry_natures(self, active_entries: list) -> None:
        """Assign nature_type and is_manual_trigger to each entry from nature.xlsx."""
        for entry in active_entries:
            # Special accounts already processed upstream - skip
            if get_account_type(entry.account_code):
                continue

            nature = self.get_nature(entry.account_code)

            if nature and nature != "manual":
                entry.nature_type = nature

            if nature == "manual":
                entry.is_manual_trigger = True

            logger.debug(
                "  Entry: account=%s, amount=%s, nature_type=%s, is_manual_trigger=%s",
                entry.account_code,
                entry.amount,
                entry.nature_type,
                entry.is_manual_trigger,
            )

    def _check_deferral(
        self,
        group: TransactionGroup,
        active_entries: list,
        bank_amount_converted: float,
    ) -> Optional[dict]:
        """Check if group should be deferred to manual or advance/settlement processing.

        Returns a result dict if deferred, None otherwise.
        """
        result = {"is_manual": False, "manual_amount": 0.0, "nature_amounts": {}}

        # Salary/bonus: defer to ManualInputProcessor
        memo = (group.bank_memo or "").lower()
        if "salary" in memo or "bonus" in memo:
            result["is_manual"] = True
            result["manual_amount"] = bank_amount_converted
            logger.debug(
                "  -> SALARY/BONUS detected in memo, deferring to ManualInputProcessor"
            )
            return result

        # Manual trigger entries (from nature.xlsx "manual" category)
        if any(e.is_manual_trigger for e in active_entries):
            result["is_manual"] = True
            result["manual_amount"] = bank_amount_converted
            logger.debug(
                "  -> MANUAL REVIEW required, amount=%s", result["manual_amount"]
            )
            return result

        return None

    def _calculate_nature_amounts(
        self,
        group: TransactionGroup,
        active_entries: list,
        ex_rate: float,
        bank_amount_converted: float,
    ) -> dict[str, float]:
        """Calculate per-nature amounts from entries. Always uses entry.amount, preserves sign."""
        nature_amounts: dict[str, float] = {}

        for entry in active_entries:
            if entry.nature_type and entry.amount:
                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                nature_amounts[entry.nature_type] = (
                    nature_amounts.get(entry.nature_type, 0.0) + amount
                )

        return nature_amounts
