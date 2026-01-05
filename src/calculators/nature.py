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
        exchange_rates: dict[str, float] = None
    ) -> tuple[dict[str, dict[str, float]], list[TransactionGroup]]:
        """
        Process all groups and calculate nature totals.
        
        Args:
            groups_by_bank: Dictionary mapping bank_identifier to list of groups
            exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
            
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
                result = self._process_group(group, ex_rate)
                
                if result["is_manual"]:
                    manual_groups.append(group)
                    nature_totals[bank_id]["manual"] += abs(result["manual_amount"])
                else:
                    for nature_key, amount in result["nature_amounts"].items():
                        if nature_key in nature_totals[bank_id]:
                            nature_totals[bank_id][nature_key] += amount
        
        return nature_totals, manual_groups
    
    def _process_group(self, group: TransactionGroup, ex_rate: float) -> dict:
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
        
        active_entries = group.active_entries
        if not active_entries:
            return result
        
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
        
        # Check if any entry requires manual review
        if any(e.is_manual_trigger for e in active_entries):
            result["is_manual"] = True
            result["manual_amount"] = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
            return result
        
        num_entries = len(active_entries)
        
        if num_entries == 1:
            # Use entry's nature, fill bank_amount
            entry = active_entries[0]
            if entry.nature_type:
                converted = convert_amount(group.bank_amount, group.bank_identifier, ex_rate)
                result["nature_amounts"][entry.nature_type] = abs(converted)
        
        elif num_entries > 1:
            # Fill each entry's amount by its nature
            for entry in active_entries:
                if entry.nature_type and entry.amount:
                    amount = abs(convert_amount(entry.amount, group.bank_identifier, ex_rate))
                    result["nature_amounts"][entry.nature_type] = \
                        result["nature_amounts"].get(entry.nature_type, 0.0) + amount
        
        return result
