"""
Nature mapper for the "by nature" section.

Maps transactions to nature categories using the lookup table.
Applies filling logic based on number of rows and nature types.

Note: Expense amounts are shown as positive values in the report.
For USD bank, amounts are converted from VND to USD using the exchange rate.
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union

from ..models import TransactionGroup, TransactionEntry, BANK_USD, BANK_VND
from ..validation import ValidationData
from .utils import get_exchange_rate, convert_amount, init_nature_totals, get_account_type
from config.mappings import DEFAULT_NATURE_LOOKUP, NATURE_CATEGORY_MAP


class NatureMapper:
    """Maps transactions to nature categories."""
    
    def __init__(self, lookup_source: Optional[Union[str, Path, BinaryIO]] = None):
        """
        Initialize with nature lookup table.
        
        Args:
            lookup_source: Path to lookup file or file-like object.
                          Uses default if not provided.
        """
        self.lookup_table = self._load_lookup_table(lookup_source)
    
    def _load_lookup_table(
        self, 
        source: Optional[Union[str, Path, BinaryIO]]
    ) -> dict[str, str]:
        """Load and parse the nature lookup table."""
        if source is None:
            source = DEFAULT_NATURE_LOOKUP
        
        df = pd.read_excel(source, header=None, engine='openpyxl')
        
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
        validation_data: Optional[ValidationData] = None
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
        validation_data: Optional[ValidationData] = None
    ) -> dict:
        """
        Process a single group for nature mapping.

        Filling logic:
        - If any entry's nature = manual OR is_manual_trigger: mark for manual review
        - If nature != manual AND 1 entry: use entry's nature, fill bank_amount
        - If nature != manual AND >1 entries: fill each entry's amount by its nature

        Returns:
            Dict with keys: is_manual, manual_amount, nature_amounts
        """
        result = {"is_manual": False, "manual_amount": 0.0, "nature_amounts": {}}

        # Priority: Check if memo contains "salary" or "bonus" - defer to manual_input.py
        memo = (group.bank_memo or "").lower()
        if "salary" in memo or "bonus" in memo:
            result["is_manual"] = True
            result["manual_amount"] = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
            print(f"  -> SALARY/BONUS detected in memo, deferring to ManualInputProcessor")
            return result

        active_entries = group.active_entries
        if not active_entries:
            return result

        # DEBUG: Print group info
        bank_amount_converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
        print(f"\n=== DEBUG _process_group (NatureMapper) ===")
        print(f"Group date: {group.date}, bank: {group.bank_identifier}")
        print(f"Bank amount: {group.bank_amount}, converted: {bank_amount_converted}")
        print(f"Active entries count: {len(active_entries)}")

        # Assign nature to each entry and check for manual triggers
        for entry in active_entries:
            nature = self.get_nature(entry.account_code)

            # If nature is "manual", don't set it as nature_type - let manual_input.py handle it
            # Only set actual nature types (org, edu, oper, etc.)
            if nature and nature != "manual":
                entry.nature_type = nature

            # Check if this is a manual trigger account (1250, 1230, 1252, 1500)
            # or if nature lookup returned "manual"
            if get_account_type(entry.account_code) or nature == "manual":
                entry.is_manual_trigger = True

            print(f"  Entry: account={entry.account_code}, amount={entry.amount}, nature_type={entry.nature_type}, is_manual_trigger={entry.is_manual_trigger}")

        # Check if any entry requires manual review
        if any(e.is_manual_trigger for e in active_entries):
            result["is_manual"] = True
            result["manual_amount"] = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
            print(f"  -> MANUAL REVIEW required, amount={result['manual_amount']}")
            return result

        num_entries = len(active_entries)

        if num_entries == 1:
            # Use entry's nature, fill bank_amount
            entry = active_entries[0]
            if entry.nature_type:
                converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
                result["nature_amounts"][entry.nature_type] = abs(converted)
                print(f"  -> SINGLE-ENTRY mode: {entry.nature_type} = abs({converted}) = {abs(converted)}")
                # Validation tracking: header row gets abs(converted) - already in USD for bank 29
                if validation_data:
                    validation_data.set_value(group.original_row_index, entry.nature_type, abs(converted))

        elif num_entries > 1:
            # Fill each entry's amount by its nature (preserve sign, no abs())
            # Sum of entry amounts equals the REVERSE of bank_amount
            print(f"  -> MULTI-ROW mode: adding entry amounts by nature (preserve sign)")
            for entry in active_entries:
                if entry.nature_type and entry.amount:
                    amount = convert_amount(entry.amount, group.bank_identifier, ex_rate)
                    old_val = result["nature_amounts"].get(entry.nature_type, 0.0)
                    result["nature_amounts"][entry.nature_type] = old_val + amount
                    print(f"    {entry.nature_type}: {old_val} + {amount} = {result['nature_amounts'][entry.nature_type]}")
                    # Validation tracking: each entry row gets converted amount (preserve sign)
                    if validation_data:
                        validation_data.set_value(entry.original_row_index, entry.nature_type, amount)

            # Validation: sum should equal reverse of bank_amount
            total = sum(result["nature_amounts"].values())
            expected = -bank_amount_converted
            print(f"  VALIDATION: sum of amounts = {total}, expected (reverse of bank) = {expected}")
            if abs(total - expected) > 1:
                print(f"  WARNING: Mismatch! Difference = {total - expected}")

        print(f"  Final result: {result}")
        return result
