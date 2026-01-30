"""
Province mapper for the "Expenditure by Province" section.

Processes transactions that have already passed through nature section,
distributing amounts to provinces based on:
1. Allocation lookup for salary/bonus transactions
2. Province code extraction from memo for standard transactions

Province section total should equal nature section total.
"""
import pandas as pd
import re
from pathlib import Path
from typing import Optional, BinaryIO, Union
from dataclasses import dataclass

from ..models import TransactionGroup, TransactionEntry, ReportSection, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount
from config.mappings import DEFAULT_STAFF_ALLOCATION_LOOKUP, PROVINCE_CODES


@dataclass
class AllocationEntry:
    """Entry from allocation lookup table."""
    name: str
    allocations: dict[str, float]  # province_code -> percentage (0.0-1.0)


# Province code patterns for extraction (uppercase for matching)
PROVINCE_PATTERNS = [
    "VNELC", "VNHBC", "VNDN", "VNQN", "VNHD", "VNQNG", "VNMOET",
    "VNBD", "VNBG", "VNLA", "VNHCM", "VNBN", "VNOTHER", "CAOBANG", "ELC"
]


class ProvinceMapper:
    """Maps transactions to province categories."""

    def __init__(
        self,
        allocation_source: Optional[Union[str, Path, BinaryIO]] = None,
    ):
        """
        Initialize with lookup tables.

        Args:
            allocation_source: Path to Staff_&_Allocation.xlsx or file-like object.
                              Uses default if not provided.
        """
        self.allocation_source = allocation_source or DEFAULT_STAFF_ALLOCATION_LOOKUP
        self.allocation_table = self._load_allocation_table()

    def _load_allocation_table(self) -> dict[str, AllocationEntry]:
        """
        Load allocation lookup from Staff_&_Allocation.xlsx.

        Structure (dec_final):
        - Sheet: "Lookup3_Staff & allocation"
        - Header row: 1 (0-indexed)
        - Column A (0): Staff Name
        - Column B (1): PACCOM nature (org/edu) - not used here
        - Columns C-K (2-10): Province allocations (VNELC, VNDN, VNQN, VNHD, VNQNg, VNHCM, VNLA, VNBN, VNMOET)

        Returns:
            Dictionary mapping lowercase name to AllocationEntry
        """
        allocation_table: dict[str, AllocationEntry] = {}

        try:
            # Try dec_final format first
            df = pd.read_excel(
                self.allocation_source,
                sheet_name="Lookup3_Staff & allocation",
                header=None
            )
            print(f"\n=== DEBUG: Loading Staff_&_Allocation.xlsx (province allocation) ===")
            print(f"  Shape: {df.shape}")

            # Find header row (row with "Staff Name")
            header_idx = 1  # Default for dec_final format
            for idx in range(min(10, len(df))):
                for val in df.iloc[idx].values:
                    if isinstance(val, str) and "staff name" in val.lower():
                        header_idx = idx
                        break

            # Get province column names from header row dynamically
            header_row = df.iloc[header_idx]
            province_cols: dict[int, str] = {}
            for col_idx in range(2, len(header_row)):
                col_name = header_row.iloc[col_idx]
                if pd.notna(col_name) and str(col_name).strip():
                    # Normalize province code to lowercase
                    province_code = str(col_name).strip().lower()
                    province_cols[col_idx] = province_code

            print(f"  Province columns: {province_cols}")

            # Parse data rows
            for idx in range(header_idx + 1, len(df)):
                row = df.iloc[idx]
                name = row.iloc[0] if len(row) > 0 else None

                if pd.isna(name) or str(name).strip() == "":
                    continue

                allocations = {}
                for col_idx, province_key in province_cols.items():
                    val = row.iloc[col_idx] if len(row) > col_idx else 0
                    if pd.notna(val) and val != 0:
                        try:
                            allocations[province_key] = float(val)
                        except (ValueError, TypeError):
                            pass

                if allocations:  # Only add if there are allocations
                    name_key = str(name).strip().lower()
                    allocation_table[name_key] = AllocationEntry(
                        name=str(name).strip(),
                        allocations=allocations
                    )

            print(f"  Loaded {len(allocation_table)} allocation entries")
            return allocation_table

        except Exception as e:
            print(f"  Could not load dec_final format: {e}")
            print(f"  Falling back to legacy format...")

        # Fallback to legacy format (dec_test allocation.xlsx)
        try:
            df = pd.read_excel(self.allocation_source, sheet_name="salary allocation-v2", header=None)
            print(f"\n=== DEBUG: Loading allocation.xlsx (legacy) ===")

            province_cols = {
                4: "vnelc",
                5: "vndn",
                6: "vnqn",
                7: "vnhd",
                8: "vnqng",
                9: "vnmoet",
            }

            header_row = 4

            for idx in range(header_row + 1, len(df)):
                row = df.iloc[idx]
                name = row.iloc[1]

                if pd.isna(name) or str(name).strip() == "":
                    continue

                allocations = {}
                for col_idx, province_key in province_cols.items():
                    val = row.iloc[col_idx] if len(row) > col_idx else 0
                    if pd.notna(val) and val != 0:
                        try:
                            allocations[province_key] = float(val)
                        except (ValueError, TypeError):
                            pass

                if allocations:
                    name_key = str(name).strip().lower()
                    allocation_table[name_key] = AllocationEntry(
                        name=str(name).strip(),
                        allocations=allocations
                    )

            print(f"  Loaded {len(allocation_table)} allocation entries")
        except Exception as e:
            print(f"Warning: Could not load allocation lookup table: {e}")

        return allocation_table

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

        # Sort patterns by length (longest first) to match more specific codes first
        sorted_patterns = sorted(PROVINCE_PATTERNS, key=len, reverse=True)

        for province in sorted_patterns:
            # Check for province code with optional OTO suffix followed by optional digits
            pattern = rf"\b{province}(?:OTO)?(?:\d+)?"
            match = re.search(pattern, memo_upper)
            if match:
                # Return normalized lowercase code
                result = province.lower()
                # Normalize VNQNg case
                if result == "vnqng":
                    result = "vnqng"
                return result

        return None

    def _lookup_allocation(self, payee_name: str) -> Optional[dict[str, float]]:
        """
        Look up allocation percentages by payee name.

        Args:
            payee_name: The payee name from the transaction

        Returns:
            Dictionary of province_code -> percentage, or None if not found
        """
        if not payee_name:
            return None

        name_lower = payee_name.strip().lower()

        # Exact match first
        if name_lower in self.allocation_table:
            return self.allocation_table[name_lower].allocations

        # Partial match - check if any staff name is contained in payee_name
        for stored_name, entry in self.allocation_table.items():
            if stored_name in name_lower or name_lower in stored_name:
                return entry.allocations

        return None

    def _init_province_totals(self) -> dict[str, dict[str, float]]:
        """Initialize province totals dictionary for both banks."""
        return {
            BANK_USD: {code: 0.0 for code in PROVINCE_CODES},
            BANK_VND: {code: 0.0 for code in PROVINCE_CODES},
        }

    def process_groups(
        self,
        groups_by_bank: dict[str, list[TransactionGroup]],
        exchange_rates: dict[str, float] = None,
        validation_data: Optional[ValidationData] = None
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

        print(f"\n=== DEBUG: ProvinceMapper.process_groups ===")

        processed_count = 0

        for bank_id, groups in groups_by_bank.items():
            for group in groups:
                # Only process groups assigned to NATURE or MANUAL sections
                if group.assigned_section not in (ReportSection.NATURE, ReportSection.MANUAL):
                    continue

                # Skip groups that were processed as settlement/advance/payable (even if assigned to MANUAL)
                # These belong to Advance/Settlement/Payable or Income sections, not province
                active_entries = group.active_entries
                if active_entries and all(
                    e.nature_type in ("settlement", "cash_settlement", "advance", "payable")
                    for e in active_entries
                ):
                    print(f"  SKIP (settlement/advance/payable): {group.payee_name} - nature_type in entries")
                    continue

                ex_rate = get_exchange_rate(group, exchange_rates)

                # Process the group
                result = self._process_group(group, ex_rate, validation_data)

                # Accumulate totals
                for province, amount in result.items():
                    if province in province_totals[bank_id]:
                        province_totals[bank_id][province] += amount
                        if amount != 0:
                            print(f"  ADD {bank_id} {province}: {amount}")

                processed_count += 1

        print(f"\n=== DEBUG: ProvinceMapper SUMMARY ===")
        print(f"Processed groups: {processed_count}")
        print(f"Province totals[VND]: { {k: v for k, v in province_totals[BANK_VND].items() if v != 0} }")
        print(f"Province totals[USD]: { {k: v for k, v in province_totals[BANK_USD].items() if v != 0} }")

        return province_totals

    def _process_group(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None
    ) -> dict[str, float]:
        """
        Process a single group for province mapping.

        Priority:
        1. Salary/Bonus: Use allocation lookup
        2. Standard: Extract province from memo
        """
        result: dict[str, float] = {}

        memo = (group.bank_memo or "").lower()
        is_salary_bonus = "salary" in memo or "bonus" in memo

        active_entries = group.active_entries
        num_entries = len(active_entries)

        bank_amount_converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
        expected_total = abs(bank_amount_converted)

        def _is_1500(entry: TransactionEntry) -> bool:
            account_code = (entry.account_code or "").upper()
            return account_code.startswith("1500")

        payable_entries = [e for e in active_entries if _is_1500(e)]
        non_payable_entries = [
            e for e in active_entries
            if not _is_1500(e) and (e.nature_type not in ("advance", "settlement", "cash_settlement"))
        ]

        non_payable_sum = sum(
            convert_amount(e.amount, group.bank_identifier, ex_rate)
            for e in non_payable_entries
            if e.amount
        )
        payable_contribution = 0.0
        for entry in payable_entries:
            if not entry.amount:
                continue
            amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
            if entry.amount < 0:
                payable_contribution += abs(amount)
            else:
                payable_contribution -= amount

        base_amount = abs(non_payable_sum)

        if is_salary_bonus:
            if base_amount == 0:
                return result
            # Priority 1: Salary/Bonus handling
            allocations = self._lookup_allocation(group.payee_name)

            if allocations:
                # Distribute by allocation percentages
                for province, percentage in allocations.items():
                    if percentage > 0:
                        amount = base_amount * percentage
                        result[province] = result.get(province, 0.0) + amount

                print(f"  SALARY/BONUS (allocation): {group.payee_name} -> {result}")
            else:
                # Fallback: Find province in memo
                province = self._extract_province_from_memo(group.bank_memo)
                if province:
                    amount = base_amount
                    result[province] = amount
                    print(f"  SALARY/BONUS (memo fallback): {group.payee_name} -> {province}: {amount}")
                else:
                    # No province found - goes to Province_Manual
                    amount = base_amount
                    result["province_manual"] = amount
                    print(f"  SALARY/BONUS (manual): {group.payee_name} -> province_manual: {amount}")
            combined = sum(result.values()) + payable_contribution
            if abs(combined - expected_total) > 0.01:
                print(f"  WARNING: Province sum mismatch! sum+payable={combined}, expected={expected_total}, diff={combined - expected_total}")

        elif num_entries == 1:
            # Single entry: Find province in memo, use abs(bank_amount)
            entry = active_entries[0]
            if _is_1500(entry):
                return result
            province = self._extract_province_from_memo(entry.original_memo)
            if not province:
                province = self._extract_province_from_memo(group.bank_memo)

            if province:
                amount = base_amount
                result[province] = amount
            else:
                # Province_Manual fallback
                amount = base_amount
                result["province_manual"] = amount

        elif num_entries > 1:
            # Multi-entry: Process each entry with preserved sign
            for entry in non_payable_entries:
                if not entry.amount:
                    continue

                # Get province from memo
                province = self._extract_province_from_memo(entry.original_memo)
                if not province:
                    province = "province_manual"

                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)

                # Normal entry: ADD to province
                result[province] = result.get(province, 0.0) + amount

            # Validation: sum should equal reverse of bank_amount
            result_sum = sum(result.values()) + payable_contribution
            if abs(result_sum - expected_total) > 0.01:  # Allow small floating point difference
                print(f"  WARNING: Province sum mismatch! sum+payable={result_sum}, expected={expected_total}, diff={result_sum - expected_total}")

        if validation_data and result:
            for province, amount in result.items():
                validation_data.set_value(group.original_row_index, province, amount)

        return result
