"""
Manual input calculator for handling 1250/1230/1252/1500 accounts.

This module handles transactions flagged based on their account nature from Nature.xlsx:
- 1230VN → "advance" → Advance section
- 1250VN → "advance" → Advance section
- 1252VN → "settlement" → Settlement section (positive bank_amount → cash_settlement)
- 1500VN → "payable" → Payable section (NEW)

The simplified 1500 logic:
- Positive 1500 entries subtract from Payable only (not from other nature categories)
- Non-1500 entries in same group use Nature.xlsx lookup normally
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union

from ..models import TransactionGroup, TransactionEntry, ReportSection, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount, normalize_nature, get_account_type, ACCOUNT_TYPES
from config.mappings import DEFAULT_STAFF_ALLOCATION_LOOKUP


class ManualInputProcessor:
    """Processes manual input transactions using lookup tables."""

    def __init__(
        self,
        staff_allocation_source: Optional[Union[str, Path, BinaryIO]] = None
    ):
        """
        Initialize with staff & allocation lookup file.

        Args:
            staff_allocation_source: Path to Staff_&_Allocation.xlsx or file-like object.
                                    Uses default if not provided.
        """
        self.staff_allocation_source = staff_allocation_source or DEFAULT_STAFF_ALLOCATION_LOOKUP
        self.staff_lookup, self.allocation_lookup = self._load_staff_allocation()

    def _load_staff_allocation(self) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
        """
        Load staff lookup and allocation tables from Staff_&_Allocation.xlsx.

        Structure (dec_final):
        - Sheet: "Lookup3_Staff & allocation"
        - Header row: 1 (0-indexed)
        - Column A (0): Staff Name
        - Column B (1): PACCOM nature (org/edu)
        - Columns C-K (2-10): Province allocations (VNELC, VNDN, VNQN, VNHD, VNQNg, VNHCM, VNLA, VNBN, VNMOET)

        Returns:
            Tuple of (staff_lookup, allocation_lookup)
            - staff_lookup: dict[name_lower] -> nature
            - allocation_lookup: dict[name_lower] -> {province: percentage}
        """
        staff_table: dict[str, str] = {}
        allocation_table: dict[str, dict[str, float]] = {}

        try:
            df = pd.read_excel(
                self.staff_allocation_source,
                sheet_name="Lookup3_Staff & allocation",
                header=None
            )
            print(f"\n=== DEBUG: Loading Staff_&_Allocation.xlsx ===")
            print(f"  Shape: {df.shape}")

            # Find header row (row with "Staff Name")
            header_idx = 1  # Default for dec_final format
            for idx in range(min(10, len(df))):
                for val in df.iloc[idx].values:
                    if isinstance(val, str) and "staff name" in val.lower():
                        header_idx = idx
                        break

            # Get province column names from header row
            header_row = df.iloc[header_idx]
            province_cols: dict[int, str] = {}
            for col_idx in range(2, len(header_row)):
                col_name = header_row.iloc[col_idx]
                if pd.notna(col_name) and str(col_name).strip():
                    # Normalize province code
                    province_code = str(col_name).strip().lower()
                    province_cols[col_idx] = province_code

            print(f"  Province columns: {province_cols}")

            # Parse data rows
            for idx in range(header_idx + 1, len(df)):
                row = df.iloc[idx]
                name = row.iloc[0] if len(row) > 0 else None
                nature_raw = row.iloc[1] if len(row) > 1 else None

                if pd.isna(name) or str(name).strip() == "":
                    continue

                name_key = str(name).strip().lower()

                # Staff nature lookup
                if pd.notna(nature_raw):
                    nature = normalize_nature(str(nature_raw))
                    staff_table[name_key] = nature

                # Allocation percentages
                allocations = {}
                for col_idx, province_code in province_cols.items():
                    val = row.iloc[col_idx] if len(row) > col_idx else 0
                    if pd.notna(val) and val != 0:
                        try:
                            allocations[province_code] = float(val)
                        except (ValueError, TypeError):
                            pass

                if allocations:
                    allocation_table[name_key] = allocations

            print(f"  Loaded {len(staff_table)} staff entries")
            print(f"  Loaded {len(allocation_table)} allocation entries")

        except Exception as e:
            print(f"Warning: Could not load staff allocation table: {e}")

        return staff_table, allocation_table

    def lookup_staff_by_name(self, payee_name: str) -> Optional[str]:
        """
        Look up nature by staff name.

        Args:
            payee_name: The payee/name from the transaction

        Returns:
            Nature category (org/edu) or None if not found
        """
        if not payee_name:
            return None

        # Try exact match (case-insensitive)
        name_lower = payee_name.strip().lower()
        if name_lower in self.staff_lookup:
            return self.staff_lookup[name_lower]

        # Try partial match - check if any staff name is contained in payee_name
        for staff_name, nature in self.staff_lookup.items():
            if staff_name in name_lower or name_lower in staff_name:
                return nature

        return None

    def lookup_allocation_by_name(self, payee_name: str) -> Optional[dict[str, float]]:
        """
        Look up allocation percentages by staff name.

        Args:
            payee_name: The payee/name from the transaction

        Returns:
            Dictionary of province -> percentage, or None if not found
        """
        if not payee_name:
            return None

        name_lower = payee_name.strip().lower()

        # Exact match first
        if name_lower in self.allocation_lookup:
            return self.allocation_lookup[name_lower]

        # Partial match
        for stored_name, allocations in self.allocation_lookup.items():
            if stored_name in name_lower or name_lower in stored_name:
                return allocations

        return None

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

        print(f"\n=== DEBUG: process_manual_groups ===")
        print(f"Total manual groups to process: {len(manual_groups)}")

        processed_count = 0
        skipped_already_processed = 0

        for i, group in enumerate(manual_groups):
            if group.is_processed:
                skipped_already_processed += 1
                print(f"  [{i}] SKIP (already processed): date={group.date}, section={group.assigned_section}")
                continue

            ex_rate = get_exchange_rate(group, exchange_rates)
            result = {"processed": False, "amounts": {}}

            # Priority 1: Check if memo contains "salary" or "bonus" - use staff lookup
            if self._is_salary_or_bonus_memo(group):
                result = self._process_salary(group, ex_rate, validation_data)
                if result["processed"]:
                    print(f"  [{i}] SALARY/BONUS: date={group.date}, name={group.payee_name}, result={result['amounts']}")
                else:
                    # Salary/bonus memo but staff not found - fall through to account type processing
                    print(f"  [{i}] SALARY/BONUS memo but staff not found: name={group.payee_name}")

            # Priority 2: Process by account type based on nature from Nature.xlsx
            if not result["processed"]:
                result = self._process_by_account_nature(group, ex_rate, validation_data)
                if result["processed"]:
                    print(f"  [{i}] ACCOUNT NATURE: date={group.date}, result={result['amounts']}")

            if result["processed"]:
                group.is_processed = True
                group.assigned_section = ReportSection.MANUAL
                processed_count += 1
                for key, amount in result["amounts"].items():
                    if key in totals[group.bank_identifier]:
                        old_val = totals[group.bank_identifier][key]
                        totals[group.bank_identifier][key] += amount
                        print(f"  [{i}] ADD {key}: {old_val} + {amount} = {totals[group.bank_identifier][key]}")
            else:
                still_manual.append(group)

        print(f"\n=== DEBUG: process_manual_groups SUMMARY ===")
        print(f"Total groups: {len(manual_groups)}")
        print(f"Skipped (already processed): {skipped_already_processed}")
        print(f"Processed in this step: {processed_count}")
        print(f"Still manual: {len(still_manual)}")
        print(f"Final totals[VND]: { {k: v for k, v in totals[BANK_VND].items() if v != 0} }")
        print(f"Final totals[USD]: { {k: v for k, v in totals[BANK_USD].items() if v != 0} }")

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
            print(f"  -> SALARY/BONUS: all entries are 1500, skipping to payable processing")
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
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, "payable", contribution)
                    print(f"  -> SALARY/1500 (negative): payable += {contribution}")
                else:
                    net_payable -= amount
                    result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) - amount
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, "payable", -amount)
                    print(f"  -> SALARY/1500 (positive): payable -= {amount}")

        # Salary = sum of non-1500 entry amounts
        salary_amount = non_payable_sum
        result["amounts"][nature] = salary_amount
        result["processed"] = True

        if validation_data:
            validation_data.set_value(group.original_row_index, nature, salary_amount)

        expected = -bank_amount_converted
        combined = non_payable_sum + net_payable
        if abs(combined - expected) > 0.01:
            print(f"  WARNING: Manual salary sum mismatch! sum+payable={combined}, expected={expected}, diff={combined - expected}")

        print(f"  -> SALARY split: {nature}={salary_amount}, payable={result['amounts'].get('payable', 0.0)}")

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

        - 1230VN / 1250VN → "advance" → Advance section (abs amount)
        - 1252VN → "settlement" → Settlement section (positive bank_amount → cash_settlement)
        - 1500VN → "payable" → Payable section (positive 1500 subtracts from payable)
        """
        result = {"processed": False, "amounts": {}}
        active_entries = group.active_entries
        bank_amount_converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)

        print(f"\n=== DEBUG _process_by_account_nature ===")
        print(f"Group date: {group.date}, bank: {group.bank_identifier}")
        print(f"Bank amount: {group.bank_amount}, converted: {bank_amount_converted}")
        print(f"Active entries: {len(active_entries)}")

        # Collect entries by their account nature
        advance_entries = []  # 1230, 1250
        settlement_entries = []  # 1252
        payable_entries = []  # 1500
        other_entries = []  # Non-special accounts

        for entry in active_entries:
            acct_type = get_account_type(entry.account_code)
            if acct_type in ("1230", "1250"):
                advance_entries.append(entry)
                entry.nature_type = "advance"
            elif acct_type == "1252":
                settlement_entries.append(entry)
                entry.nature_type = "settlement"
            elif acct_type == "1500":
                payable_entries.append(entry)
                entry.nature_type = "payable"
            else:
                # Keep existing nature_type from NatureMapper
                other_entries.append(entry)

            print(f"  Entry: account={entry.account_code}, amount={entry.amount}, nature={entry.nature_type}")

        # Determine processing mode
        has_payable = len(payable_entries) > 0
        has_advance = len(advance_entries) > 0
        has_settlement = len(settlement_entries) > 0
        num_entries = len(active_entries)

        # Single-entry groups: use abs(bank_amount)
        if num_entries == 1:
            entry = active_entries[0]
            nature = entry.nature_type

            if nature == "advance":
                converted = abs(bank_amount_converted)
                result["amounts"]["advance"] = converted
                result["processed"] = True
                if validation_data:
                    validation_data.set_value(group.original_row_index, "advance", converted)
                print(f"  -> SINGLE ADVANCE: {converted}")

            elif nature == "settlement":
                # Positive bank_amount → cash_settlement (Income section)
                if group.bank_amount > 0:
                    converted = abs(bank_amount_converted)
                    result["amounts"]["cash_settlement"] = converted
                    entry.nature_type = "cash_settlement"
                    if validation_data:
                        validation_data.set_value(group.original_row_index, "cash_settlement", converted)
                    print(f"  -> SINGLE SETTLEMENT (positive) → cash_settlement: {converted}")
                else:
                    converted = abs(bank_amount_converted)
                    result["amounts"]["settlement"] = converted
                    if validation_data:
                        validation_data.set_value(group.original_row_index, "settlement", converted)
                    print(f"  -> SINGLE SETTLEMENT: {converted}")
                result["processed"] = True

            elif nature == "payable":
                converted = abs(bank_amount_converted)
                result["amounts"]["payable"] = converted
                result["processed"] = True
                if validation_data:
                    validation_data.set_value(group.original_row_index, "payable", converted)
                print(f"  -> SINGLE PAYABLE: {converted}")

        # Multi-entry groups
        elif num_entries > 1:
            result["processed"] = True

            # Process other entries (non-special accounts) - use their nature from NatureMapper
            for entry in other_entries:
                if entry.amount and entry.nature_type:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    result["amounts"][entry.nature_type] = result["amounts"].get(entry.nature_type, 0.0) + amount
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, entry.nature_type, amount)
                    print(f"  -> OTHER entry: {entry.nature_type} += {amount}")

            # Process advance entries (1230/1250)
            for entry in advance_entries:
                if entry.amount:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    result["amounts"]["advance"] = result["amounts"].get("advance", 0.0) + abs(amount)
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, "advance", abs(amount))
                    print(f"  -> ADVANCE entry: advance += abs({amount})")

            # Process settlement entries (1252)
            for entry in settlement_entries:
                if entry.amount:
                    # Positive bank_amount → cash_settlement
                    if group.bank_amount > 0:
                        amount = abs(convert_amount(entry.amount, group.bank_identifier, ex_rate))
                        result["amounts"]["cash_settlement"] = result["amounts"].get("cash_settlement", 0.0) + amount
                        entry.nature_type = "cash_settlement"
                        if validation_data:
                            validation_data.set_value(entry.original_row_index, "cash_settlement", amount)
                        print(f"  -> SETTLEMENT (positive) → cash_settlement += {amount}")
                    else:
                        amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                        result["amounts"]["settlement"] = result["amounts"].get("settlement", 0.0) + abs(amount)
                        if validation_data:
                            validation_data.set_value(entry.original_row_index, "settlement", abs(amount))
                        print(f"  -> SETTLEMENT entry: settlement += abs({amount})")

            non_payable_sum = sum(
                convert_amount(entry.amount, group.bank_identifier, ex_rate)
                for entry in active_entries
                if entry.amount and get_account_type(entry.account_code) != "1500"
            )

            # Process payable entries (1500) - SIMPLIFIED LOGIC
            # Negative 1500: ADD to payable
            # Positive 1500: SUBTRACT from payable
            net_payable = 0.0
            for entry in payable_entries:
                if entry.amount:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    if entry.amount < 0:
                        # Negative 1500: add absolute value
                        result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) + abs(amount)
                        net_payable += abs(amount)
                        if validation_data:
                            validation_data.set_value(entry.original_row_index, "payable", abs(amount))
                        print(f"  -> PAYABLE (negative) entry: payable += abs({amount})")
                    else:
                        # Positive 1500: subtract
                        result["amounts"]["payable"] = result["amounts"].get("payable", 0.0) - amount
                        net_payable -= amount
                        if validation_data:
                            validation_data.set_value(entry.original_row_index, "payable", -amount)
                        print(f"  -> PAYABLE (positive) entry: payable -= {amount}")

            if payable_entries and any(get_account_type(e.account_code) != "1500" for e in active_entries):
                expected = -bank_amount_converted
                combined = non_payable_sum + net_payable
                if abs(combined - expected) > 0.01:
                    print(f"  WARNING: Manual sum mismatch! sum+payable={combined}, expected={expected}, diff={combined - expected}")

        print(f"  Final result: {result}")
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
    # Income checks
    if group.is_deposit():
        if group.any_name_contains("onesky"):
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
