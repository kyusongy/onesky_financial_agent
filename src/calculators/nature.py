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

from ..models import TransactionGroup, TransactionRow, BankType
from .utils import get_exchange_rate, convert_amount, init_nature_totals
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
        groups_by_bank: dict[BankType, list[TransactionGroup]],
        exchange_rates: dict[str, float] = None
    ) -> tuple[dict[BankType, dict[str, float]], list[TransactionGroup]]:
        """
        Process all groups and calculate nature totals.
        
        Args:
            groups_by_bank: Dictionary mapping BankType to list of groups
            exchange_rates: Dictionary of date string to exchange rate (for USD conversion)
            
        Returns:
            Tuple of (nature_totals by bank, list of manual review groups)
        """
        nature_totals: dict[BankType, dict[str, float]] = {
            BankType.USD: init_nature_totals(),
            BankType.VND: init_nature_totals(),
        }
        manual_groups: list[TransactionGroup] = []
        
        for bank_type, groups in groups_by_bank.items():
            for group in groups:
                ex_rate = get_exchange_rate(group, exchange_rates)
                result = self._process_group(group, ex_rate)
                
                if result["is_manual"]:
                    group.requires_manual_review = True
                    manual_groups.append(group)
                    nature_totals[bank_type]["manual"] += abs(result["manual_amount"])
                else:
                    for nature_key, amount in result["nature_amounts"].items():
                        if nature_key in nature_totals[bank_type]:
                            nature_totals[bank_type][nature_key] += amount
        
        return nature_totals, manual_groups
    
    def _process_group(self, group: TransactionGroup, ex_rate: float) -> dict:
        """
        Process a single group for nature mapping.
        
        Filling logic:
        - If any row's nature = manual: mark for manual review, use first row's amount
        - If nature != manual AND 2 rows: use 2nd row's nature, fill 1st row's amount
        - If nature != manual AND >2 rows: fill each row's amount by its nature
        
        Returns:
            Dict with keys: is_manual, manual_amount, nature_amounts
        """
        result = {"is_manual": False, "manual_amount": 0.0, "nature_amounts": {}}
        
        if not group.rows:
            return result
        
        # Assign nature to each non-bank-entry row
        for row in group.rows:
            if not row.is_bank_entry():
                row.nature_category = self.get_nature(row.get_account_number())
        
        # Check if any row requires manual review
        if any(row.nature_category == "manual" for row in group.rows):
            result["is_manual"] = True
            if group.first_row and group.first_row.amount is not None:
                result["manual_amount"] = convert_amount(group.first_row.amount, ex_rate)
            return result
        
        num_rows = len(group.rows)
        
        if num_rows == 2:
            # Use 2nd row's nature, fill 1st row's amount
            first_row, second_row = group.rows[0], group.rows[1]
            if second_row.nature_category and first_row.amount is not None:
                converted = convert_amount(first_row.amount, ex_rate)
                result["nature_amounts"][second_row.nature_category] = abs(converted)
        
        elif num_rows > 2:
            # Fill each detail row's amount by its nature
            for row in group.detail_rows:
                if row.nature_category and row.amount is not None:
                    amount = abs(convert_amount(row.amount, ex_rate))
                    result["nature_amounts"][row.nature_category] = \
                        result["nature_amounts"].get(row.nature_category, 0.0) + amount
        
        return result
