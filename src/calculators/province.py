"""
Province mapper for the "Expenditure by Province" section.

Processes transactions that have already passed through nature section,
distributing amounts to provinces based on:
1. Allocation lookup for salary/bonus transactions
2. Province code extraction from memo for standard transactions
3. PIT lookup file for ignored tax/insurance transactions

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
from config.mappings import DEFAULT_STAFF_ALLOCATION_LOOKUP, DEFAULT_PIT_LOOKUP, PROVINCE_CODES


@dataclass
class AllocationEntry:
    """Entry from allocation lookup table."""
    name: str
    allocations: dict[str, float]  # province_code -> percentage (0.0-1.0)


@dataclass
class PITEntry:
    """Entry from PIT lookup table."""
    name: str
    memo: str
    amount: float
    group: int = 1  # 1 or 2 based on position in file (separated by empty row)


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
        pit_source: Optional[Union[str, Path, BinaryIO]] = None,
    ):
        """
        Initialize with lookup tables.

        Args:
            allocation_source: Path to Staff_&_Allocation.xlsx or file-like object.
                              Uses default if not provided.
            pit_source: Path to PIT_SI.xlsx or file-like object.
                       Uses default if not provided.
        """
        self.allocation_source = allocation_source or DEFAULT_STAFF_ALLOCATION_LOOKUP
        self.pit_source = pit_source or DEFAULT_PIT_LOOKUP
        self.allocation_table = self._load_allocation_table()
        self.pit_entries = self._load_pit_lookup()

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

    def _load_pit_lookup(self) -> list[PITEntry]:
        """
        Load PIT lookup from PIT_SI.xlsx.

        Structure (dec_final - 5 columns):
        - Column A (0): Staff Name (company name for grouping)
        - Column B (1): Transaction Date
        - Column C (2): Name (individual staff name)
        - Column D (3): Memo/Description (contains province codes)
        - Column E (4): Amount

        Entries are grouped by company name:
        - "Service Center for Foreign Affairs" → SI/HI transactions
        - "Vietnam Tax Company" → PIT transactions

        Returns:
            List of PITEntry objects
        """
        entries: list[PITEntry] = []

        try:
            df = pd.read_excel(self.pit_source, sheet_name="PIT+SI payment", header=None)
            print(f"\n=== DEBUG: Loading PIT_SI.xlsx ===")
            print(f"  Shape: {df.shape}")

            # Find header row (row with column names like "Staff Name")
            header_row = 0
            for idx in range(min(5, len(df))):
                row = df.iloc[idx]
                if pd.notna(row.iloc[0]) and "staff name" in str(row.iloc[0]).lower():
                    header_row = idx
                    break

            print(f"  Header row: {header_row}")

            # dec_final format: 5 fixed columns
            # Column 0: Staff Name (company), Column 2: Name (person), Column 3: Memo, Column 4: Amount
            company_col = 0
            name_col = 2
            memo_col = 3
            amount_col = 4

            # Group by company name
            current_company = None

            for idx in range(header_row + 1, len(df)):
                row = df.iloc[idx]
                company = row.iloc[company_col] if len(row) > company_col else None
                name = row.iloc[name_col] if len(row) > name_col else None
                memo = row.iloc[memo_col] if len(row) > memo_col else None
                amount = row.iloc[amount_col] if len(row) > amount_col else None

                # Skip separator/instruction rows
                if pd.isna(company) or str(company).strip() == "-":
                    continue
                if pd.notna(company) and "input payment" in str(company).lower():
                    continue

                # Track company for grouping
                company_str = str(company).strip().lower() if pd.notna(company) else ""

                # Determine group based on company name
                if "service center" in company_str:
                    group = 1  # SI/HI/UI transactions
                elif "vietnam tax" in company_str:
                    group = 2  # PIT transactions
                else:
                    continue  # Skip unknown companies

                # Skip if no valid amount
                if pd.isna(amount) or amount == 0:
                    continue

                # Get name - could be in company column or name column
                person_name = str(name).strip() if pd.notna(name) else ""
                if not person_name:
                    continue

                try:
                    entries.append(PITEntry(
                        name=person_name,
                        memo=str(memo) if pd.notna(memo) else "",
                        amount=float(amount),
                        group=group
                    ))
                except (ValueError, TypeError):
                    pass

            # Debug: show group counts
            group1_count = sum(1 for e in entries if e.group == 1)
            group2_count = sum(1 for e in entries if e.group == 2)
            group1_sum = sum(e.amount for e in entries if e.group == 1)
            group2_sum = sum(e.amount for e in entries if e.group == 2)
            print(f"  Loaded {len(entries)} PIT entries")
            print(f"  Group 1 (Service Center - SI/HI): {group1_count} entries, sum: {group1_sum:,.0f}")
            print(f"  Group 2 (Vietnam Tax - PIT): {group2_count} entries, sum: {group2_sum:,.0f}")
        except Exception as e:
            print(f"Warning: Could not load PIT lookup table: {e}")

        return entries

    def _should_ignore_group(self, group: TransactionGroup) -> bool:
        """
        Check if transaction should be ignored from province calculation.

        Ignore if:
        - Name = "Vietnam Tax Company" AND memo contains "PIT"
        - Name = "Service Center for Foreign Affairs" AND memo contains "SI", "HI", or "UI"

        Returns:
            True if group should be ignored (will be handled via PIT lookup)
        """
        name = (group.payee_name or "").lower()
        memo = (group.bank_memo or "").lower()

        if "vietnam tax company" in name and "pit" in memo:
            return True

        if "service center for foreign affairs" in name:
            if any(kw in memo for kw in [" si", " hi", " ui", "si ", "hi ", "ui "]):
                return True
            # Also check for standalone SI/HI/UI
            if re.search(r'\b(si|hi|ui)\b', memo):
                return True

        return False

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
    ) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
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
            Tuple of (province_totals by bank, pit_totals by bank)
        """
        province_totals = self._init_province_totals()

        print(f"\n=== DEBUG: ProvinceMapper.process_groups ===")

        processed_count = 0
        ignored_count = 0
        ignored_groups: list[TransactionGroup] = []  # Track ignored groups for PIT processing

        for bank_id, groups in groups_by_bank.items():
            for group in groups:
                # Only process groups assigned to NATURE or MANUAL sections
                if group.assigned_section not in (ReportSection.NATURE, ReportSection.MANUAL):
                    continue

                # Skip groups that were processed as settlement/advance/payable (even if assigned to MANUAL)
                # These belong to Advance/Settlement/Payable or Income sections, not province
                active_entries = group.active_entries
                if any(e.nature_type in ("settlement", "cash_settlement", "advance", "payable") for e in active_entries):
                    print(f"  SKIP (settlement/advance/payable): {group.payee_name} - nature_type in entries")
                    continue

                ex_rate = get_exchange_rate(group, exchange_rates)

                # Check if should ignore (for PIT lookup processing)
                if self._should_ignore_group(group):
                    ignored_groups.append(group)  # Store for PIT validation tracking
                    ignored_count += 1
                    print(f"  IGNORE: {group.payee_name} - {group.bank_memo[:50] if group.bank_memo else ''}")
                    continue

                # Process the group
                result = self._process_group(group, ex_rate, validation_data)

                # Accumulate totals
                for province, amount in result.items():
                    if province in province_totals[bank_id]:
                        province_totals[bank_id][province] += amount
                        if amount != 0:
                            print(f"  ADD {bank_id} {province}: {amount}")

                processed_count += 1

        # Process PIT lookup entries (VND Bank 30 only)
        # Pass ignored groups so we can record validation data on their rows
        pit_totals = self._process_pit_entries(ignored_groups, validation_data)

        print(f"\n=== DEBUG: ProvinceMapper SUMMARY ===")
        print(f"Processed groups: {processed_count}")
        print(f"Ignored groups (handled by PIT): {ignored_count}")
        print(f"Province totals[VND]: { {k: v for k, v in province_totals[BANK_VND].items() if v != 0} }")
        print(f"Province totals[USD]: { {k: v for k, v in province_totals[BANK_USD].items() if v != 0} }")
        print(f"PIT totals[VND]: { {k: v for k, v in pit_totals[BANK_VND].items() if v != 0} }")

        return province_totals, pit_totals

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

        if is_salary_bonus:
            # Priority 1: Salary/Bonus handling
            allocations = self._lookup_allocation(group.payee_name)

            if allocations:
                # Distribute by allocation percentages
                bank_amount_abs = abs(bank_amount_converted)

                for province, percentage in allocations.items():
                    if percentage > 0:
                        amount = bank_amount_abs * percentage
                        result[province] = result.get(province, 0.0) + amount

                        if validation_data:
                            # Track on header row - accumulate if multiple provinces
                            existing = validation_data.get_value(group.original_row_index, province)
                            if existing is not None:
                                validation_data.set_value(group.original_row_index, province, existing + amount)
                            else:
                                validation_data.set_value(group.original_row_index, province, amount)

                print(f"  SALARY/BONUS (allocation): {group.payee_name} -> {result}")
            else:
                # Fallback: Find province in memo
                province = self._extract_province_from_memo(group.bank_memo)
                if province:
                    amount = abs(bank_amount_converted)
                    result[province] = amount
                    if validation_data:
                        validation_data.set_value(group.original_row_index, province, amount)
                    print(f"  SALARY/BONUS (memo fallback): {group.payee_name} -> {province}: {amount}")
                else:
                    # No province found - goes to Province_Manual
                    amount = abs(bank_amount_converted)
                    result["province_manual"] = amount
                    if validation_data:
                        validation_data.set_value(group.original_row_index, "province_manual", amount)
                    print(f"  SALARY/BONUS (manual): {group.payee_name} -> province_manual: {amount}")

        elif num_entries == 1:
            # Single entry: Find province in memo, use abs(bank_amount)
            entry = active_entries[0]
            province = self._extract_province_from_memo(entry.original_memo)
            if not province:
                province = self._extract_province_from_memo(group.bank_memo)

            if province:
                amount = abs(bank_amount_converted)
                result[province] = amount
                if validation_data:
                    validation_data.set_value(group.original_row_index, province, amount)
            else:
                # Province_Manual fallback
                amount = abs(bank_amount_converted)
                result["province_manual"] = amount
                if validation_data:
                    validation_data.set_value(group.original_row_index, "province_manual", amount)

        elif num_entries > 1:
            # Multi-entry: Process each entry with preserved sign
            # BUT positive 1500 entries should be SUBTRACTED (same logic as manual_input.py)
            prev_province = None

            for entry in active_entries:
                if not entry.amount:
                    continue

                # Check if this is a 1500 account
                account_code = (entry.account_code or "").upper()
                is_1500 = account_code.startswith("1500")
                is_positive_1500 = is_1500 and entry.amount > 0

                # Get province from memo
                province = self._extract_province_from_memo(entry.original_memo)
                if not province:
                    if is_positive_1500 and prev_province:
                        # Positive 1500 inherits province from previous row
                        province = prev_province
                    else:
                        province = "province_manual"

                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)

                if is_positive_1500:
                    # Positive 1500: SUBTRACT from province (same as nature logic)
                    result[province] = result.get(province, 0.0) - amount
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, province, -amount)
                else:
                    # Normal entry: ADD to province
                    result[province] = result.get(province, 0.0) + amount
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, province, amount)
                    # Update prev_province for positive 1500 inheritance
                    prev_province = province

            # Validation: sum should equal reverse of bank_amount
            result_sum = sum(result.values())
            expected = abs(bank_amount_converted)
            if abs(result_sum - expected) > 0.01:  # Allow small floating point difference
                print(f"  WARNING: Province sum mismatch! sum={result_sum}, expected={expected}, diff={result_sum - expected}")

        return result

    def _process_pit_entries(
        self,
        ignored_groups: list[TransactionGroup],
        validation_data: Optional[ValidationData] = None
    ) -> dict[str, dict[str, float]]:
        """
        Process PIT lookup entries.

        For each PIT entry:
        1. Look up name in allocation_lookup
        2. If found: Distribute abs(amount) by allocation percentages
        3. If not found: Find province in memo
        4. If not found AND memo contains "consultant": Province_Manual

        PIT entries are divided into groups (separated by empty rows in PIT_lookup.xlsx):
        - Group 1 sum matches Vietnam Tax Company transaction
        - Group 2 sum matches Service Center transaction

        Validation data is recorded on respective transaction rows based on group matching.

        Args:
            ignored_groups: List of ignored transaction groups (Vietnam Tax/PIT, Service Center/SI/HI/UI)
            validation_data: Optional ValidationData to track per-row contributions

        Returns:
            Province totals from PIT entries by bank (VND only)
        """
        pit_totals = self._init_province_totals()

        # PIT entries are VND only (Bank 30)
        bank_id = BANK_VND

        print(f"\n=== DEBUG: Processing {len(self.pit_entries)} PIT entries ===")

        # Track province amounts by group for validation
        group_province_totals: dict[int, dict[str, float]] = {
            1: {code: 0.0 for code in PROVINCE_CODES},
            2: {code: 0.0 for code in PROVINCE_CODES},
        }

        for entry in self.pit_entries:
            if entry.amount == 0:
                continue

            amount_abs = abs(entry.amount)
            group = entry.group

            # Try allocation lookup first
            allocations = self._lookup_allocation(entry.name)

            if allocations:
                for province, percentage in allocations.items():
                    if percentage > 0:
                        distributed = amount_abs * percentage
                        pit_totals[bank_id][province] += distributed
                        group_province_totals[group][province] += distributed
                print(f"  PIT (allocation) G{group}: {entry.name} -> distributed to {list(allocations.keys())}")
            else:
                # Try province from memo
                province = self._extract_province_from_memo(entry.memo)

                if province:
                    pit_totals[bank_id][province] += amount_abs
                    group_province_totals[group][province] += amount_abs
                    print(f"  PIT (memo) G{group}: {entry.name} -> {province}: {amount_abs}")
                elif "consultant" in entry.memo.lower():
                    pit_totals[bank_id]["province_manual"] += amount_abs
                    group_province_totals[group]["province_manual"] += amount_abs
                    print(f"  PIT (consultant) G{group}: {entry.name} -> province_manual: {amount_abs}")
                else:
                    # Default fallback
                    pit_totals[bank_id]["province_manual"] += amount_abs
                    group_province_totals[group]["province_manual"] += amount_abs
                    print(f"  PIT (fallback) G{group}: {entry.name} -> province_manual: {amount_abs}")

        # Match PIT groups to ignored transactions by bank_amount
        group1_sum = sum(abs(e.amount) for e in self.pit_entries if e.group == 1)
        group2_sum = sum(abs(e.amount) for e in self.pit_entries if e.group == 2)

        print(f"\n=== DEBUG: Matching PIT groups to transactions ===")
        print(f"  Group 1 sum: {group1_sum:,.0f}")
        print(f"  Group 2 sum: {group2_sum:,.0f}")

        group_to_transaction: dict[int, TransactionGroup] = {}
        for ig in ignored_groups:
            bank_abs = abs(ig.bank_amount)
            print(f"  Ignored transaction: row={ig.original_row_index}, name='{ig.payee_name}', amount={bank_abs:,.0f}")
            if abs(bank_abs - group1_sum) < 1.0:
                group_to_transaction[1] = ig
                print(f"    -> Matched to Group 1")
            elif abs(bank_abs - group2_sum) < 1.0:
                group_to_transaction[2] = ig
                print(f"    -> Matched to Group 2")

        # Record validation data on respective transaction rows
        if validation_data:
            print(f"\n=== DEBUG: Recording PIT validation on respective rows ===")
            for group_num, province_amounts in group_province_totals.items():
                if group_num in group_to_transaction:
                    row_index = group_to_transaction[group_num].original_row_index
                    for province, amount in province_amounts.items():
                        if amount != 0:
                            validation_data.set_value(row_index, province, amount)
                            print(f"  Set validation: row={row_index}, G{group_num}, {province}={amount:,.0f}")
                else:
                    print(f"  WARNING: No matching transaction for Group {group_num}")

        return pit_totals
