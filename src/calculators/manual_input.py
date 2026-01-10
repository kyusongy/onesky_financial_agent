"""
Manual input calculator for handling 1250/1230/1252/1500 accounts.

This module handles transactions flagged as "manual input" in the nature lookup.
Uses Manual.xlsx lookup file with 4 tabs (1250, 1230, 1252, 1500).

Priority order (to avoid double counting):
1. Income
2. Advance/Settlement
3. By Nature (!=manual)
4. By Nature (=manual) - handled here
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union
from dataclasses import dataclass

from ..models import TransactionGroup, TransactionEntry, ReportSection, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount, normalize_nature, get_account_type, ACCOUNT_TYPES


# Default path for manual lookup
DEFAULT_MANUAL_LOOKUP = Path(__file__).parent.parent.parent / "instruction_data" / "templates" / "Manual.xlsx"

# Default path for staff lookup
DEFAULT_STAFF_LOOKUP = Path(__file__).parent.parent.parent / "instruction_data" / "templates" / "staff_lookup.xlsx"

# Column indices in Manual.xlsx sheets
MANUAL_COL_AMOUNT = 8
MANUAL_COL_PROVINCE = 13
MANUAL_COL_NATURE = 14

# Column indices in staff_lookup.xlsx (after header row)
STAFF_COL_NAME = 1
STAFF_COL_NATURE = 2


@dataclass
class ManualLookupEntry:
    """Entry from manual lookup table."""
    amount: float
    nature: str  # "advance", "settlement", or nature category
    province: Optional[str] = None


class ManualInputProcessor:
    """Processes manual input transactions using lookup tables."""

    def __init__(
        self,
        lookup_source: Optional[Union[str, Path, BinaryIO]] = None,
        staff_lookup_source: Optional[Union[str, Path, BinaryIO]] = None
    ):
        """
        Initialize with manual lookup file and staff lookup file.

        Args:
            lookup_source: Path to Manual.xlsx or file-like object.
                          Uses default if not provided.
            staff_lookup_source: Path to staff_lookup.xlsx or file-like object.
                                Uses default if not provided.
        """
        self.lookup_source = lookup_source or DEFAULT_MANUAL_LOOKUP
        self.staff_lookup_source = staff_lookup_source or DEFAULT_STAFF_LOOKUP
        self.lookup_tables = self._load_lookup_tables()
        self.staff_lookup = self._load_staff_lookup()
    
    def _load_lookup_tables(self) -> dict[str, list[ManualLookupEntry]]:
        """Load all 4 sheets from Manual.xlsx."""
        tables = {acct: [] for acct in ACCOUNT_TYPES}
        
        try:
            xlsx = pd.ExcelFile(self.lookup_source)
            print(f"\n=== DEBUG: Loading Manual.xlsx ===")
            print(f"Available sheets: {xlsx.sheet_names}")
            for sheet_name in ACCOUNT_TYPES:
                if sheet_name in xlsx.sheet_names:
                    df = pd.read_excel(self.lookup_source, sheet_name=sheet_name, header=None)
                    tables[sheet_name] = self._parse_sheet(df)
                    print(f"  Loaded {sheet_name}: {len(tables[sheet_name])} entries")
                else:
                    print(f"  Sheet {sheet_name} NOT FOUND")
        except Exception as e:
            print(f"Warning: Could not load manual lookup table: {e}")
        
        return tables
    
    def _parse_sheet(self, df: pd.DataFrame) -> list[ManualLookupEntry]:
        """Parse a single sheet from Manual.xlsx."""
        entries = []
        header_idx = self._find_header_row(df)
        
        for idx in range(header_idx + 1, len(df)):
            row = df.iloc[idx]
            amount = row.iloc[MANUAL_COL_AMOUNT] if len(row) > MANUAL_COL_AMOUNT else None
            nature_raw = row.iloc[MANUAL_COL_NATURE] if len(row) > MANUAL_COL_NATURE else None
            province = row.iloc[MANUAL_COL_PROVINCE] if len(row) > MANUAL_COL_PROVINCE else None
            
            if pd.notna(amount) and pd.notna(nature_raw):
                entries.append(ManualLookupEntry(
                    amount=float(amount),
                    nature=normalize_nature(str(nature_raw)),
                    province=str(province) if pd.notna(province) else None,
                ))
        
        return entries
    
    def _find_header_row(self, df: pd.DataFrame, default: int = 7) -> int:
        """Find header row containing 'Date'."""
        for idx in range(min(15, len(df))):
            for val in df.iloc[idx].values:
                if isinstance(val, str) and val.lower().strip() == "date":
                    return idx
        return default

    def _load_staff_lookup(self) -> dict[str, str]:
        """
        Load staff lookup table from staff_lookup.xlsx.

        Returns:
            Dictionary mapping staff name (lowercase) to nature (org/edu)
        """
        staff_table: dict[str, str] = {}

        try:
            df = pd.read_excel(self.staff_lookup_source, sheet_name="Staff", header=None)
            print(f"\n=== DEBUG: Loading staff_lookup.xlsx ===")

            # Find header row (row with "Staff name")
            header_idx = 0
            for idx in range(min(10, len(df))):
                for val in df.iloc[idx].values:
                    if isinstance(val, str) and "staff name" in val.lower():
                        header_idx = idx
                        break

            # Parse data rows
            for idx in range(header_idx + 1, len(df)):
                row = df.iloc[idx]
                name = row.iloc[STAFF_COL_NAME] if len(row) > STAFF_COL_NAME else None
                nature_raw = row.iloc[STAFF_COL_NATURE] if len(row) > STAFF_COL_NATURE else None

                if pd.notna(name) and pd.notna(nature_raw):
                    # Store lowercase name for case-insensitive matching
                    name_key = str(name).strip().lower()
                    nature = normalize_nature(str(nature_raw))
                    staff_table[name_key] = nature

            print(f"  Loaded {len(staff_table)} staff entries")
        except Exception as e:
            print(f"Warning: Could not load staff lookup table: {e}")

        return staff_table

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

    def lookup_by_amount(self, account_type: str, amount: float) -> Optional[str]:
        """
        Look up nature by amount in the specified account type table.
        
        Args:
            account_type: "1250", "1230", "1252", or "1500"
            amount: Amount to match
            
        Returns:
            Nature category or None if not found
        """
        entries = self.lookup_tables.get(account_type, [])
        
        # DEBUG: Show lookup table info
        if account_type == "1500":
            print(f"    [lookup_by_amount] Looking for amount={amount} in {account_type} table ({len(entries)} entries)")
        
        # First try exact match
        for entry in entries:
            if abs(entry.amount - amount) < 0.01:
                return entry.nature
        
        # Also try matching absolute values (in case signs differ)
        for entry in entries:
            if abs(abs(entry.amount) - abs(amount)) < 0.01:
                return entry.nature
        
        # DEBUG: Show first few entries if no match found
        if account_type == "1500" and len(entries) > 0:
            print(f"    [lookup_by_amount] No match found. First 5 entries in table:")
            for i, e in enumerate(entries[:5]):
                print(f"      {i}: amount={e.amount}, nature={e.nature}")
        
        return None
    
    def process_manual_groups(
        self,
        manual_groups: list[TransactionGroup],
        exchange_rates: dict[str, float] = None,
        validation_data: Optional[ValidationData] = None
    ) -> tuple[dict[str, dict[str, float]], list[TransactionGroup]]:
        """
        Process manual input groups and calculate nature totals.

        Args:
            manual_groups: List of transaction groups flagged for manual processing
            exchange_rates: Dictionary of date string to exchange rate
            validation_data: Optional ValidationData to track per-row contributions

        Returns:
            Tuple of (nature_totals by bank, groups still needing manual review)
        """
        nature_totals: dict[str, dict[str, float]] = {
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

            # Priority 2: Process by account type (if not already processed by salary logic)
            if not result["processed"]:
                account_type = self._get_group_account_type(group)

                if account_type in ("1250", "1230", "1252"):
                    result = self._process_1250_1230_1252(group, account_type, ex_rate, validation_data)
                elif account_type == "1500":
                    result = self._process_1500(group, ex_rate, validation_data)
                else:
                    print(f"  [{i}] SKIP (no account type): date={group.date}")

            if result["processed"]:
                group.is_processed = True
                group.assigned_section = ReportSection.MANUAL
                processed_count += 1
                for nature_key, amount in result["amounts"].items():
                    if nature_key in nature_totals[group.bank_identifier]:
                        old_val = nature_totals[group.bank_identifier][nature_key]
                        nature_totals[group.bank_identifier][nature_key] += amount
                        print(f"  [{i}] ADD {nature_key}: {old_val} + {amount} = {nature_totals[group.bank_identifier][nature_key]}")
            else:
                still_manual.append(group)

        print(f"\n=== DEBUG: process_manual_groups SUMMARY ===")
        print(f"Total groups: {len(manual_groups)}")
        print(f"Skipped (already processed): {skipped_already_processed}")
        print(f"Processed in this step: {processed_count}")
        print(f"Still manual: {len(still_manual)}")
        print(f"Final nature_totals[VND]:")
        for k, v in nature_totals[BANK_VND].items():
            if v != 0:
                print(f"  {k}: {v}")
        print(f"Final nature_totals[USD]:")
        for k, v in nature_totals[BANK_USD].items():
            if v != 0:
                print(f"  {k}: {v}")

        return nature_totals, still_manual
    
    def _init_totals(self) -> dict[str, float]:
        """Initialize totals dictionary with all nature categories."""
        return {
            "org": 0.0, "edu": 0.0, "oper": 0.0,
            "nutrition": 0.0, "edu_infra": 0.0,
            "advance": 0.0, "settlement": 0.0,
            "cash_settlement": 0.0,
        }
    
    def _get_group_account_type(self, group: TransactionGroup) -> Optional[str]:
        """Determine account type (1250, 1230, 1252, 1500) from group."""
        for entry in group.entries:
            acct_type = get_account_type(entry.account_code)
            if acct_type:
                return acct_type
        return None

    def _is_salary_or_bonus_memo(self, group: TransactionGroup) -> bool:
        """
        Check if header memo contains "salary" or "bonus".

        Handles cases like:
        - "salary" anywhere in memo
        - "bonus" anywhere in memo
        - ".bonus" (with period before bonus)
        """
        memo = group.bank_memo.lower() if group.bank_memo else ""
        if "salary" in memo:
            return True
        if "bonus" in memo:
            return True
        return False

    def _process_salary(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None
    ) -> dict:
        """
        Process salary/bonus transactions by looking up staff name.

        If header memo contains "salary" or "bonus", look up payee_name in staff_lookup.xlsx
        to determine nature (org/edu). Use absolute bank_amount for the result.

        Returns:
            Dict with keys: processed, amounts
        """
        result = {"processed": False, "amounts": {}}

        # Look up staff by payee name
        nature = self.lookup_staff_by_name(group.payee_name)

        if nature:
            # Use absolute bank_amount for the nature category
            converted = abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
            result["amounts"][nature] = converted
            result["processed"] = True

            # Validation tracking: header row gets converted amount (USD for bank 29)
            if validation_data:
                validation_data.set_value(group.original_row_index, nature, converted)

            # Mark all entries with this nature for reporting purposes
            for entry in group.entries:
                entry.nature_type = nature

        return result

    def _process_1250_1230_1252(
        self,
        group: TransactionGroup,
        account_type: str,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None
    ) -> dict:
        """
        Process 1250/1230/1252 groups.

        For 1-entry groups: look up nature by amount, enter absolute bank value.
        For >1 entries with 1500: defer to 1500 logic.
        For >1 entries without 1500: process each entry individually.
        """
        result = {"processed": False, "amounts": {}}
        active_entries = group.active_entries
        num_entries = len(active_entries)

        # DEBUG: Print group info
        bank_amount_converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
        print(f"\n=== DEBUG _process_1250_1230_1252 ===")
        print(f"Group date: {group.date}, bank: {group.bank_identifier}")
        print(f"Account type: {account_type}")
        print(f"Bank amount: {group.bank_amount}, converted: {bank_amount_converted}")
        print(f"Active entries count: {num_entries}")
        for entry in active_entries:
            print(f"  Entry: account={entry.account_code}, amount={entry.amount}, nature_type={entry.nature_type}")

        if num_entries == 1:
            entry = active_entries[0]
            if entry.amount:
                nature = self.lookup_by_amount(account_type, entry.amount)
                print(f"  -> SINGLE-ENTRY mode: lookup({account_type}, {entry.amount}) = {nature}")
                if nature:
                    # If lookup returns "settlement" but bank_amount > 0, route to cash_settlement (Income)
                    if nature == "settlement" and group.bank_amount > 0:
                        nature = "cash_settlement"
                        print(f"  -> Positive settlement routed to cash_settlement (Income)")
                    converted = abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
                    result["amounts"][nature] = converted
                    result["processed"] = True
                    entry.nature_type = nature
                    print(f"  -> Result: {nature} = abs(bank_amount) = {converted}")
                    # Validation tracking: header row gets converted amount (USD for bank 29)
                    if validation_data:
                        validation_data.set_value(group.original_row_index, nature, converted)

        elif num_entries > 1:
            # Check if any entry is 1500
            has_1500 = any(get_account_type(e.account_code) == "1500" for e in active_entries)

            if has_1500:
                print(f"  -> Has 1500 entry, deferring to _process_1500")
                return self._process_1500(group, ex_rate, validation_data)

            # Process each entry individually (preserve sign for multi-row)
            print(f"  -> MULTI-ROW mode (no 1500): processing each entry")
            for entry in active_entries:
                if entry.amount:
                    nature = self.lookup_by_amount(account_type, entry.amount)
                    print(f"    lookup({account_type}, {entry.amount}) = {nature}")
                    if nature:
                        # If lookup returns "settlement" but bank_amount > 0, route to cash_settlement (Income)
                        if nature == "settlement" and group.bank_amount > 0:
                            nature = "cash_settlement"
                            print(f"    -> Positive settlement routed to cash_settlement (Income)")
                        amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                        old_val = result["amounts"].get(nature, 0.0)
                        result["amounts"][nature] = old_val + amount
                        entry.nature_type = nature
                        result["processed"] = True
                        print(f"    {nature}: {old_val} + {amount} = {result['amounts'][nature]}")
                        # Validation tracking: each entry row gets converted amount (preserve sign)
                        if validation_data:
                            validation_data.set_value(entry.original_row_index, nature, amount)

            # Validation for multi-row
            if result["amounts"]:
                total = sum(result["amounts"].values())
                expected = -bank_amount_converted
                print(f"  VALIDATION: sum = {total}, expected (reverse of bank) = {expected}")
                if abs(total - expected) > 1:
                    print(f"  WARNING: Mismatch! Difference = {total - expected}")

        print(f"  Final result: {result}")
        return result
    
    def _process_1500(
        self,
        group: TransactionGroup,
        ex_rate: float,
        validation_data: Optional[ValidationData] = None
    ) -> dict:
        """
        Process 1500 groups.

        Single-entry groups (just negative 1500):
            - Look up nature, enter absolute bank_amount

        Multi-entry groups:
            - Add each non-1500 entry's amount (WITH SIGN) to its nature
            - Add negative 1500 entry's amount (WITH SIGN) to its nature
            - Subtract positive 1500 amounts from their nature
            - Validate: sum of nature amounts should equal bank_amount
        """
        result = {"processed": False, "amounts": {}}
        bank_amount_raw = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
        bank_amount_abs = abs(bank_amount_raw)
        active_entries = group.active_entries

        # DEBUG: Print group info
        print(f"\n=== DEBUG _process_1500 ===")
        print(f"Group date: {group.date}, bank: {group.bank_identifier}")
        print(f"Bank amount: {group.bank_amount}, converted: {bank_amount_raw}")
        print(f"Active entries count: {len(active_entries)}")

        # Check if this is a single-entry group (just one negative 1500)
        is_single_entry = len(active_entries) == 1

        # First pass: determine nature for non-1500 entries
        # Use existing nature_type from NatureMapper, OR lookup in Manual.xlsx ONLY for manual trigger accounts
        # Track prev_nature for positive 1500 inheritance
        prev_nature = None
        for entry in active_entries:
            if get_account_type(entry.account_code) != "1500" and entry.amount:
                # Only look up Manual.xlsx for manual trigger accounts (1250/1230/1252)
                # Don't overwrite nature_type for non-manual accounts (e.g., 71204VN)
                entry_acct_type = get_account_type(entry.account_code)
                if entry_acct_type in ("1250", "1230", "1252"):
                    lookup_nature = self.lookup_by_amount(entry_acct_type, entry.amount)
                    if lookup_nature:
                        # If lookup returns "settlement" but bank_amount > 0, route to cash_settlement
                        if lookup_nature == "settlement" and group.bank_amount > 0:
                            lookup_nature = "cash_settlement"
                            print(f"    -> Positive settlement routed to cash_settlement (Income)")
                        entry.nature_type = lookup_nature

                # Update prev_nature from existing nature_type (from NatureMapper or lookup)
                if entry.nature_type:
                    prev_nature = entry.nature_type
                    print(f"  Non-1500 entry: account={entry.account_code}, nature={entry.nature_type}, prev_nature updated")

        # Second pass: process 1500 entries and determine their nature
        negative_1500_entries = []
        positive_1500_entries = []

        for entry in active_entries:
            is_1500 = get_account_type(entry.account_code) == "1500"
            if not is_1500 or not entry.amount:
                # For non-1500 entries, update prev_nature as we iterate (maintains order)
                if entry.nature_type:
                    prev_nature = entry.nature_type
                continue

            if entry.amount < 0:
                print(f"  Negative 1500: account={entry.account_code}, amount={entry.amount}")
                nature = self.lookup_by_amount("1500", entry.amount)
                print(f"    Lookup result: {nature}")
                if nature:
                    # If lookup returns "settlement" but bank_amount > 0, route to cash_settlement
                    if nature == "settlement" and group.bank_amount > 0:
                        nature = "cash_settlement"
                        print(f"    -> Positive settlement routed to cash_settlement (Income)")
                    entry.nature_type = nature
                    prev_nature = nature
                    negative_1500_entries.append(entry)
                    print(f"    -> Added to negative_1500_entries")
                else:
                    # Default to org if not found
                    entry.nature_type = "org"
                    prev_nature = "org"
                    negative_1500_entries.append(entry)
                    print(f"    -> Defaulted to org")
            elif entry.amount > 0:
                # Positive 1500: PIT/SI/HI → org, else inherit from PREVIOUS ROW
                if any(entry.memo_contains(kw) for kw in ("pit", "si", "hi")):
                    entry.nature_type = "org"
                elif prev_nature:
                    entry.nature_type = prev_nature
                else:
                    entry.nature_type = "org"  # Default
                positive_1500_entries.append(entry)
                prev_nature = entry.nature_type  # Update for next iteration
                print(f"  Positive 1500: account={entry.account_code}, amount={entry.amount}, nature={entry.nature_type}")

        # Calculate amounts based on single vs multi-entry logic
        if is_single_entry and negative_1500_entries:
            # Single-entry: use absolute bank_amount
            entry = negative_1500_entries[0]
            result["amounts"][entry.nature_type] = bank_amount_abs
            result["processed"] = True
            print(f"  Single-entry mode: {entry.nature_type} = {bank_amount_abs}")
            # Validation tracking: header row gets converted amount (USD for bank 29)
            if validation_data:
                validation_data.set_value(group.original_row_index, entry.nature_type, bank_amount_abs)
        else:
            # Multi-entry: add each entry's amount WITH SIGN

            # Add non-1500 entry amounts (preserve sign)
            for entry in active_entries:
                if not entry.amount or not entry.nature_type:
                    continue
                if get_account_type(entry.account_code) == "1500":
                    continue

                amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                result["amounts"][entry.nature_type] = \
                    result["amounts"].get(entry.nature_type, 0.0) + amount
                result["processed"] = True
                print(f"  Non-1500 entry: {entry.nature_type} += {amount}")
                # Validation tracking: entry row gets converted amount (preserve sign)
                if validation_data:
                    validation_data.set_value(entry.original_row_index, entry.nature_type, amount)

            # Add negative 1500 entry amounts (preserve sign - they're negative)
            for entry in negative_1500_entries:
                if entry.nature_type and entry.amount:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    result["amounts"][entry.nature_type] = \
                        result["amounts"].get(entry.nature_type, 0.0) + amount
                    result["processed"] = True
                    print(f"  Negative 1500: {entry.nature_type} += {amount}")
                    # Validation tracking: entry row gets converted amount (negative)
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, entry.nature_type, amount)

            # Subtract positive 1500 amounts
            for entry in positive_1500_entries:
                if entry.nature_type and entry.amount:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    result["amounts"][entry.nature_type] = \
                        result["amounts"].get(entry.nature_type, 0.0) - amount
                    print(f"  Positive 1500 subtract: {entry.nature_type} -= {amount}")
                    # Validation tracking: entry row gets NEGATIVE of converted amount (since subtracted)
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, entry.nature_type, -amount)

        if result["amounts"]:
            result["processed"] = True

        # DEBUG: Print final result and validate
        print(f"  Final result: processed={result['processed']}, amounts={result['amounts']}")
        print(f"  Negative 1500 count: {len(negative_1500_entries)}")

        # Sanity check: sum of amounts should equal REVERSE of bank_amount (for multi-entry)
        # e.g., if bank_amount = -14,343,323, sum should be +14,343,323
        if not is_single_entry and result["amounts"]:
            total = sum(result["amounts"].values())
            expected = -bank_amount_raw  # Reverse of bank_amount
            print(f"  VALIDATION: sum of amounts = {total}, expected (reverse of bank) = {expected}")
            if abs(total - expected) > 1:  # Allow small rounding error
                print(f"  WARNING: Mismatch! Difference = {total - expected}")
        
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
    # This prevents false positives from entries containing "advance" in their memo
    # Settlement only goes here if bank_amount <= 0
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
    if any(e.nature_type and e.nature_type != "manual" for e in active_entries):
        return ReportSection.NATURE

    return None
