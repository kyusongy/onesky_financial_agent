"""
Nature mapper for the "by nature" section.

Maps transactions to nature categories using the lookup table.
Applies filling logic based on number of rows and nature types.
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union

from ..models import TransactionGroup, TransactionRow, BankType
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
    
    def process_groups(
        self,
        groups_by_bank: dict[BankType, list[TransactionGroup]]
    ) -> tuple[dict[BankType, dict[str, float]], list[TransactionGroup]]:
        """
        Process all groups and calculate nature totals.
        
        Args:
            groups_by_bank: Dictionary mapping BankType to list of groups
            
        Returns:
            Tuple of (nature_totals by bank, list of manual review groups)
        """
        nature_totals: dict[BankType, dict[str, float]] = {
            BankType.USD: self._init_nature_totals(),
            BankType.VND: self._init_nature_totals(),
        }
        manual_groups: list[TransactionGroup] = []
        
        for bank_type, groups in groups_by_bank.items():
            for group in groups:
                result = self._process_group(group)
                
                if result["is_manual"]:
                    group.requires_manual_review = True
                    manual_groups.append(group)
                    # Add to manual input total
                    nature_totals[bank_type]["manual"] += result["manual_amount"]
                else:
                    # Add to respective nature categories
                    for nature_key, amount in result["nature_amounts"].items():
                        if nature_key in nature_totals[bank_type]:
                            nature_totals[bank_type][nature_key] += amount
        
        return nature_totals, manual_groups
    
    def _init_nature_totals(self) -> dict[str, float]:
        """Initialize nature totals dictionary."""
        return {
            "org": 0.0,
            "edu": 0.0,
            "oper": 0.0,
            "nutrition": 0.0,
            "edu_infra": 0.0,
            "manual": 0.0,
        }
    
    def _process_group(self, group: TransactionGroup) -> dict:
        """
        Process a single group for nature mapping.
        
        Filling logic:
        - If any row's nature = manual: mark for manual review, use first row's amount
        - If nature != manual AND 2 rows: use 2nd row's nature, fill 1st row's amount
        - If nature != manual AND >2 rows: fill each row's amount by its nature (opposite sign)
        
        Returns:
            Dict with keys: is_manual, manual_amount, nature_amounts
        """
        result = {
            "is_manual": False,
            "manual_amount": 0.0,
            "nature_amounts": {},
        }
        
        if not group.rows:
            return result
        
        # Assign nature to each row
        for row in group.rows:
            account_num = row.get_account_number()
            row.nature_category = self.get_nature(account_num)
        
        # Check if any row requires manual review
        has_manual = any(row.nature_category == "manual" for row in group.rows)
        
        if has_manual:
            result["is_manual"] = True
            # Use first row's amount for manual input
            if group.first_row and group.first_row.amount is not None:
                result["manual_amount"] = group.first_row.amount
            return result
        
        # Process based on number of rows
        num_rows = len(group.rows)
        
        if num_rows == 2:
            # Use 2nd row's nature, fill 1st row's amount
            first_row = group.rows[0]
            second_row = group.rows[1]
            
            if second_row.nature_category and first_row.amount is not None:
                result["nature_amounts"][second_row.nature_category] = first_row.amount
        
        elif num_rows > 2:
            # Fill each row's amount by its nature (opposite sign)
            # First row is the bank actual input, skip it
            # Use other rows' amounts with opposite sign
            for row in group.detail_rows:
                if row.nature_category and row.amount is not None:
                    # Take opposite sign (negate the amount)
                    amount = -row.amount
                    if row.nature_category in result["nature_amounts"]:
                        result["nature_amounts"][row.nature_category] += amount
                    else:
                        result["nature_amounts"][row.nature_category] = amount
        
        return result

