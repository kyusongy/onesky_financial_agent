"""
Manual input calculator for handling 1250/1230/1252/1500 accounts.

This module handles transactions that are flagged as "manual input" in the 
nature lookup table. It uses a separate Manual.xlsx lookup file with 4 tabs
(1250, 1230, 1252, 1500) to determine the nature of these transactions.

Priority order (to avoid double counting):
1. Income
2. Advance/Settlement
3. By Nature (!=manual)
4. By Nature (=manual) - handled here
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union
from dataclasses import dataclass, field

from ..models import TransactionGroup, TransactionRow, BankType
from config.mappings import NATURE_CATEGORY_MAP


# Default path for manual lookup
DEFAULT_MANUAL_LOOKUP = Path(__file__).parent.parent.parent / "instruction_data" / "templates" / "Manual.xlsx"


@dataclass
class ManualLookupEntry:
    """Entry from manual lookup table."""
    amount: float
    nature: str  # "advance", "settlement", or nature category like "edu", "org", "oper"
    province: Optional[str] = None


class ManualInputProcessor:
    """Processes manual input transactions using lookup tables."""
    
    def __init__(self, lookup_source: Optional[Union[str, Path, BinaryIO]] = None):
        """
        Initialize with manual lookup file.
        
        Args:
            lookup_source: Path to Manual.xlsx or file-like object.
                          Uses default if not provided.
        """
        self.lookup_source = lookup_source or DEFAULT_MANUAL_LOOKUP
        self.lookup_tables = self._load_lookup_tables()
    
    def _load_lookup_tables(self) -> dict[str, list[ManualLookupEntry]]:
        """Load all 4 sheets from Manual.xlsx."""
        tables = {
            "1250": [],
            "1230": [],
            "1252": [],
            "1500": [],
        }
        
        try:
            xlsx = pd.ExcelFile(self.lookup_source)
            
            for sheet_name in ["1250", "1230", "1252", "1500"]:
                if sheet_name in xlsx.sheet_names:
                    df = pd.read_excel(self.lookup_source, sheet_name=sheet_name, header=None)
                    entries = self._parse_sheet(df, sheet_name)
                    tables[sheet_name] = entries
        except Exception as e:
            print(f"Warning: Could not load manual lookup table: {e}")
        
        return tables
    
    def _parse_sheet(self, df: pd.DataFrame, sheet_name: str) -> list[ManualLookupEntry]:
        """Parse a single sheet from Manual.xlsx."""
        entries = []
        
        # Find header row (contains "Date")
        header_idx = 7  # Default
        for idx in range(min(15, len(df))):
            row = df.iloc[idx]
            for val in row.values:
                if isinstance(val, str) and val.lower().strip() == "date":
                    header_idx = idx
                    break
        
        # Parse data rows (after header)
        for idx in range(header_idx + 1, len(df)):
            row = df.iloc[idx]
            
            # Amount is in column 8 (index 8)
            amount = row.iloc[8] if len(row) > 8 else None
            
            # Nature/PACCOM is in column 14 (index 14)
            nature_raw = row.iloc[14] if len(row) > 14 else None
            
            # Province is in column 13 (index 13)
            province = row.iloc[13] if len(row) > 13 else None
            
            if pd.notna(amount) and pd.notna(nature_raw):
                nature = self._normalize_nature(str(nature_raw))
                entries.append(ManualLookupEntry(
                    amount=float(amount),
                    nature=nature,
                    province=str(province) if pd.notna(province) else None,
                ))
        
        return entries
    
    def _normalize_nature(self, nature_raw: str) -> str:
        """Normalize nature string to category key."""
        nature_lower = nature_raw.lower().strip()
        
        # Check for advance/settlement
        if "advance" in nature_lower:
            return "advance"
        if "settlement" in nature_lower:
            return "settlement"
        
        # Check nature categories
        if "org" in nature_lower:
            return "org"
        if "edu" in nature_lower:
            return "edu"
        if "oper" in nature_lower:
            return "oper"
        if "nutrition" in nature_lower:
            return "nutrition"
        if "infra" in nature_lower:
            return "edu_infra"
        
        return nature_lower
    
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
        
        for entry in entries:
            if abs(entry.amount - amount) < 0.01:
                return entry.nature
        
        return None
    
    def process_manual_groups(
        self,
        manual_groups: list[TransactionGroup],
        exchange_rates: dict[str, float] = None
    ) -> tuple[dict[BankType, dict[str, float]], list[TransactionGroup]]:
        """
        Process manual input groups and calculate nature totals.
        
        Args:
            manual_groups: Groups flagged as requiring manual input
            exchange_rates: Exchange rates for USD conversion
            
        Returns:
            Tuple of (nature_totals by bank, groups still needing manual review)
        """
        nature_totals: dict[BankType, dict[str, float]] = {
            BankType.USD: self._init_totals(),
            BankType.VND: self._init_totals(),
        }
        still_manual: list[TransactionGroup] = []
        
        for group in manual_groups:
            # Skip if already processed
            if getattr(group, 'is_processed', False):
                continue
            
            # Get exchange rate
            ex_rate = self._get_exchange_rate(group, exchange_rates)
            
            # Determine account type from first detail row
            account_type = self._get_account_type(group)
            
            if account_type in ["1250", "1230", "1252"]:
                result = self._process_1250_1230_1252(group, account_type, ex_rate)
            elif account_type == "1500":
                result = self._process_1500(group, ex_rate)
            else:
                # Unknown account type, keep as manual
                result = {"processed": False, "amounts": {}}
            
            if result["processed"]:
                group.is_processed = True
                for nature_key, amount in result["amounts"].items():
                    if nature_key in nature_totals[group.bank_type]:
                        nature_totals[group.bank_type][nature_key] += amount
                    elif nature_key in ["advance", "settlement"]:
                        # These go to advance/settlement section
                        pass  # Handled separately
            else:
                still_manual.append(group)
        
        return nature_totals, still_manual
    
    def _init_totals(self) -> dict[str, float]:
        """Initialize totals dictionary."""
        return {
            "org": 0.0,
            "edu": 0.0,
            "oper": 0.0,
            "nutrition": 0.0,
            "edu_infra": 0.0,
            "advance": 0.0,
            "settlement": 0.0,
        }
    
    def _get_exchange_rate(
        self,
        group: TransactionGroup,
        exchange_rates: dict[str, float]
    ) -> float:
        """Get exchange rate for USD bank."""
        if group.bank_type != BankType.USD:
            return 1.0
        if not exchange_rates or not group.date:
            return 1.0
        date_key = group.date.strftime("%Y-%m-%d")
        return exchange_rates.get(date_key, 1.0)
    
    def _convert_amount(self, amount: float, ex_rate: float) -> float:
        """Convert amount using exchange rate."""
        if ex_rate <= 0:
            return amount
        return amount / ex_rate
    
    def _get_account_type(self, group: TransactionGroup) -> Optional[str]:
        """Determine account type (1250, 1230, 1252, 1500) from group."""
        for row in group.rows:
            account_num = row.get_account_number()
            if account_num:
                account_lower = account_num.lower()
                if "1250" in account_lower:
                    return "1250"
                if "1230" in account_lower:
                    return "1230"
                if "1252" in account_lower:
                    return "1252"
                if "1500" in account_lower:
                    return "1500"
        return None
    
    def _process_1250_1230_1252(
        self,
        group: TransactionGroup,
        account_type: str,
        ex_rate: float
    ) -> dict:
        """
        Process 1250/1230/1252 groups.
        
        For 2-row groups: look up nature by amount, enter absolute bank value.
        For >2 rows: defer to 1500 logic (return not processed).
        """
        result = {"processed": False, "amounts": {}}
        
        num_rows = len(group.rows)
        
        if num_rows == 2:
            # Look up nature for the detail row's amount
            detail_row = group.detail_rows[0] if group.detail_rows else None
            if detail_row and detail_row.amount is not None:
                nature = self.lookup_by_amount(account_type, detail_row.amount)
                
                if nature:
                    # Use absolute value of first row (bank entry)
                    bank_amount = abs(self._convert_amount(group.first_amount, ex_rate))
                    result["amounts"][nature] = bank_amount
                    result["processed"] = True
                    
                    # Assign nature to row for display
                    detail_row.nature_category = nature
        
        # For >2 rows, we need to check if there's a 1500 row
        elif num_rows > 2:
            # Check if any row is 1500
            has_1500 = any(
                "1500" in (row.get_account_number() or "").lower()
                for row in group.rows
            )
            
            if has_1500:
                # Process with 1500 logic
                return self._process_1500(group, ex_rate)
            else:
                # Process each row individually
                for row in group.detail_rows:
                    if row.amount is not None:
                        nature = self.lookup_by_amount(account_type, row.amount)
                        if nature:
                            amount = abs(self._convert_amount(row.amount, ex_rate))
                            if nature in result["amounts"]:
                                result["amounts"][nature] += amount
                            else:
                                result["amounts"][nature] = amount
                            row.nature_category = nature
                            result["processed"] = True
        
        return result
    
    def _process_1500(self, group: TransactionGroup, ex_rate: float) -> dict:
        """
        Process 1500 groups.
        
        1. Negative 1500: look up nature from table, enter ABSOLUTE BANK AMOUNT into report
        2. Positive 1500: if memo contains PIT/SI/HI → Org, else use previous row's nature
        3. For multi-row: enter each row's amount by nature, subtract positive 1500
        """
        result = {"processed": False, "amounts": {}}
        
        # Get absolute bank amount (first row)
        bank_amount = abs(self._convert_amount(group.first_amount, ex_rate))
        
        # First pass: determine nature for all rows
        prev_nature = None
        negative_1500_rows = []
        
        for row in group.detail_rows:
            account_num = row.get_account_number() or ""
            is_1500 = "1500" in account_num.lower()
            
            if is_1500:
                if row.amount is not None and row.amount < 0:
                    # Negative 1500: look up nature
                    nature = self.lookup_by_amount("1500", row.amount)
                    row.nature_category = nature
                    if nature:
                        prev_nature = nature
                        negative_1500_rows.append(row)
                elif row.amount is not None and row.amount > 0:
                    # Positive 1500: check memo for PIT/SI/HI
                    if row.memo_contains("pit") or row.memo_contains("si") or row.memo_contains("hi"):
                        row.nature_category = "org"
                    else:
                        # Use previous row's nature
                        row.nature_category = prev_nature
            else:
                # Non-1500 row: try to determine nature from lookup
                if row.amount is not None:
                    for acct_type in ["1250", "1230", "1252"]:
                        nature = self.lookup_by_amount(acct_type, row.amount)
                        if nature:
                            row.nature_category = nature
                            prev_nature = nature
                            break
        
        # For negative 1500: enter the absolute BANK AMOUNT into the report
        # (This is the key difference - we use bank_amount, not the row's amount)
        for row in negative_1500_rows:
            if row.nature_category:
                nature = row.nature_category
                if nature in result["amounts"]:
                    result["amounts"][nature] += bank_amount
                else:
                    result["amounts"][nature] = bank_amount
                result["processed"] = True
        
        # Second pass: calculate amounts for non-1500 rows and handle positive 1500
        positive_1500_by_nature: dict[str, float] = {}
        
        for row in group.detail_rows:
            if row.amount is None or not row.nature_category:
                continue
            
            account_num = row.get_account_number() or ""
            is_1500 = "1500" in account_num.lower()
            
            # Skip negative 1500 (already handled above with bank_amount)
            if is_1500 and row.amount < 0:
                continue
            
            converted = self._convert_amount(row.amount, ex_rate)
            
            if is_1500 and row.amount > 0:
                # Track positive 1500 to subtract later
                nature = row.nature_category
                if nature in positive_1500_by_nature:
                    positive_1500_by_nature[nature] += abs(converted)
                else:
                    positive_1500_by_nature[nature] = abs(converted)
            else:
                # Non-1500 row: add the actual amount (using absolute value for expenses)
                nature = row.nature_category
                amount = abs(converted)
                if nature in result["amounts"]:
                    result["amounts"][nature] += amount
                else:
                    result["amounts"][nature] = amount
                result["processed"] = True
        
        # Subtract positive 1500 amounts
        for nature, subtract_amount in positive_1500_by_nature.items():
            if nature in result["amounts"]:
                result["amounts"][nature] -= subtract_amount
        
        # Mark as processed if we have any amounts
        if result["amounts"]:
            result["processed"] = True
        
        return result


def mark_processed_groups(
    groups_by_bank: dict[BankType, list[TransactionGroup]],
    income: dict[BankType, dict[str, float]],
    advance_settlement: dict[BankType, dict[str, float]],
    nature_totals: dict[BankType, dict[str, float]]
) -> None:
    """
    Mark groups as processed based on what sections they contributed to.
    
    This helps avoid double counting when processing manual input.
    
    Args:
        groups_by_bank: All transaction groups
        income: Income totals (to identify income groups)
        advance_settlement: Advance/settlement totals
        nature_totals: Nature totals (excluding manual)
    """
    for bank_type, groups in groups_by_bank.items():
        for group in groups:
            # Check if group contributed to income
            if _contributed_to_income(group):
                group.is_processed = True
                group.processed_section = "income"
                continue
            
            # Check if group contributed to advance/settlement
            if _contributed_to_advance_settlement(group):
                group.is_processed = True
                group.processed_section = "advance_settlement"
                continue
            
            # Check if group contributed to nature (non-manual)
            if _contributed_to_nature_non_manual(group):
                group.is_processed = True
                group.processed_section = "nature"
                continue
            
            # Otherwise, not processed yet (may be manual)
            group.is_processed = False
            group.processed_section = None


def _contributed_to_income(group: TransactionGroup) -> bool:
    """Check if group contributed to income section."""
    # Contribution: deposit + name contains onesky
    if group.has_deposit_type() and group.any_name_contains("onesky"):
        return True
    
    # Fund transfer: memo contains transfer but not ELC
    if group.any_memo_contains("transfer") and not group.any_memo_contains("elc"):
        return True
    
    # Interest: deposit + memo contains interest
    if group.has_deposit_type() and group.any_memo_contains("interest"):
        return True
    
    # PED: deposit + memo contains PED
    if group.has_deposit_type() and group.any_memo_contains("ped"):
        return True
    
    return False


def _contributed_to_advance_settlement(group: TransactionGroup) -> bool:
    """Check if group contributed to advance/settlement section."""
    # Advance: memo contains advance but not settlement
    if group.any_memo_contains("advance") and not group.any_memo_contains("settlement"):
        return True
    
    # Settlement: memo contains settlement
    if group.any_memo_contains("settlement"):
        return True
    
    return False


def _contributed_to_nature_non_manual(group: TransactionGroup) -> bool:
    """Check if group contributed to nature section (non-manual)."""
    # Check if any detail row has a non-manual nature category
    for row in group.detail_rows:
        if row.nature_category and row.nature_category != "manual":
            return True
    return False

