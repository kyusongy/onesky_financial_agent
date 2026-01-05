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

from ..models import TransactionGroup, TransactionRow, BankType
from .utils import get_exchange_rate, convert_amount, normalize_nature, get_account_type, ACCOUNT_TYPES


# Default path for manual lookup
DEFAULT_MANUAL_LOOKUP = Path(__file__).parent.parent.parent / "instruction_data" / "templates" / "Manual.xlsx"

# Column indices in Manual.xlsx sheets
MANUAL_COL_AMOUNT = 8
MANUAL_COL_PROVINCE = 13
MANUAL_COL_NATURE = 14


@dataclass
class ManualLookupEntry:
    """Entry from manual lookup table."""
    amount: float
    nature: str  # "advance", "settlement", or nature category
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
        tables = {acct: [] for acct in ACCOUNT_TYPES}
        
        try:
            xlsx = pd.ExcelFile(self.lookup_source)
            for sheet_name in ACCOUNT_TYPES:
                if sheet_name in xlsx.sheet_names:
                    df = pd.read_excel(self.lookup_source, sheet_name=sheet_name, header=None)
                    tables[sheet_name] = self._parse_sheet(df)
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
        
        # First try exact match
        for entry in entries:
            if abs(entry.amount - amount) < 0.01:
                return entry.nature
        
        # Also try matching absolute values (in case signs differ)
        for entry in entries:
            if abs(abs(entry.amount) - abs(amount)) < 0.01:
                return entry.nature
        
        return None
    
    def process_manual_groups(
        self,
        manual_groups: list[TransactionGroup],
        exchange_rates: dict[str, float] = None
    ) -> tuple[dict[BankType, dict[str, float]], list[TransactionGroup]]:
        """
        Process manual input groups and calculate nature totals.
        
        Returns:
            Tuple of (nature_totals by bank, groups still needing manual review)
        """
        nature_totals: dict[BankType, dict[str, float]] = {
            BankType.USD: self._init_totals(),
            BankType.VND: self._init_totals(),
        }
        still_manual: list[TransactionGroup] = []
        
        for group in manual_groups:
            if getattr(group, 'is_processed', False):
                continue
            
            ex_rate = get_exchange_rate(group, exchange_rates)
            account_type = self._get_group_account_type(group)
            
            if account_type in ("1250", "1230", "1252"):
                result = self._process_1250_1230_1252(group, account_type, ex_rate)
            elif account_type == "1500":
                result = self._process_1500(group, ex_rate)
            else:
                result = {"processed": False, "amounts": {}}
            
            if result["processed"]:
                group.is_processed = True
                group.processed_section = "manual"
                for nature_key, amount in result["amounts"].items():
                    if nature_key in nature_totals[group.bank_type]:
                        nature_totals[group.bank_type][nature_key] += amount
            else:
                still_manual.append(group)
        
        return nature_totals, still_manual
    
    def _init_totals(self) -> dict[str, float]:
        """Initialize totals dictionary with all nature categories."""
        return {
            "org": 0.0, "edu": 0.0, "oper": 0.0,
            "nutrition": 0.0, "edu_infra": 0.0,
            "advance": 0.0, "settlement": 0.0,
        }
    
    def _get_group_account_type(self, group: TransactionGroup) -> Optional[str]:
        """Determine account type (1250, 1230, 1252, 1500) from group."""
        for row in group.rows:
            acct_type = get_account_type(row.get_account_number())
            if acct_type:
                return acct_type
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
        For >2 rows with 1500: defer to 1500 logic.
        For >2 rows without 1500: process each row individually.
        """
        result = {"processed": False, "amounts": {}}
        num_rows = len(group.rows)
        
        if num_rows == 2:
            detail_row = group.detail_rows[0] if group.detail_rows else None
            if detail_row and detail_row.amount is not None:
                nature = self.lookup_by_amount(account_type, detail_row.amount)
                if nature:
                    result["amounts"][nature] = abs(convert_amount(group.first_amount, ex_rate))
                    result["processed"] = True
                    detail_row.nature_category = nature
        
        elif num_rows > 2:
            # Check if any row is 1500
            has_1500 = any(get_account_type(row.get_account_number()) == "1500" for row in group.rows)
            
            if has_1500:
                return self._process_1500(group, ex_rate)
            
            # Process each row individually
            for row in group.detail_rows:
                if row.amount is not None:
                    nature = self.lookup_by_amount(account_type, row.amount)
                    if nature:
                        amount = abs(convert_amount(row.amount, ex_rate))
                        result["amounts"][nature] = result["amounts"].get(nature, 0.0) + amount
                        row.nature_category = nature
                        result["processed"] = True
        
        return result
    
    def _process_1500(self, group: TransactionGroup, ex_rate: float) -> dict:
        """
        Process 1500 groups.
        
        1. Negative 1500: look up nature, enter ABSOLUTE BANK AMOUNT
        2. Positive 1500: if memo contains PIT/SI/HI → Org, else use previous row's nature
        3. For multi-row: enter each row's amount by nature, subtract positive 1500
        """
        result = {"processed": False, "amounts": {}}
        bank_amount = abs(convert_amount(group.first_amount, ex_rate))
        
        # First pass: determine nature for non-1500 rows
        prev_nature = None
        for row in group.detail_rows:
            if get_account_type(row.get_account_number()) != "1500" and row.amount is not None:
                for acct_type in ("1250", "1230", "1252"):
                    nature = self.lookup_by_amount(acct_type, row.amount)
                    if nature:
                        row.nature_category = nature
                        prev_nature = nature
                        break
        
        # Second pass: process 1500 rows
        negative_1500_rows = []
        positive_1500_by_nature: dict[str, float] = {}
        
        for row in group.detail_rows:
            is_1500 = get_account_type(row.get_account_number()) == "1500"
            if not is_1500 or row.amount is None:
                continue
            
            if row.amount < 0:
                nature = self.lookup_by_amount("1500", row.amount)
                if nature:
                    row.nature_category = nature
                    prev_nature = nature
                    negative_1500_rows.append(row)
            elif row.amount > 0:
                if any(row.memo_contains(kw) for kw in ("pit", "si", "hi")):
                    row.nature_category = "org"
                elif prev_nature:
                    row.nature_category = prev_nature
                
                if row.nature_category:
                    amt = abs(convert_amount(row.amount, ex_rate))
                    positive_1500_by_nature[row.nature_category] = \
                        positive_1500_by_nature.get(row.nature_category, 0.0) + amt
        
        # Add negative 1500 amounts using bank_amount
        for row in negative_1500_rows:
            if row.nature_category:
                result["amounts"][row.nature_category] = \
                    result["amounts"].get(row.nature_category, 0.0) + bank_amount
                result["processed"] = True
        
        # Add non-1500 row amounts
        for row in group.detail_rows:
            if row.amount is None or not row.nature_category:
                continue
            is_1500 = get_account_type(row.get_account_number()) == "1500"
            if is_1500:
                continue
            
            amount = abs(convert_amount(row.amount, ex_rate))
            result["amounts"][row.nature_category] = \
                result["amounts"].get(row.nature_category, 0.0) + amount
            result["processed"] = True
        
        # Subtract positive 1500 amounts
        for nature, subtract_amt in positive_1500_by_nature.items():
            if nature in result["amounts"]:
                result["amounts"][nature] -= subtract_amt
        
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
    Helps avoid double counting when processing manual input.
    """
    for groups in groups_by_bank.values():
        for group in groups:
            section = _get_processed_section(group)
            group.is_processed = section is not None
            group.processed_section = section


def _get_processed_section(group: TransactionGroup) -> Optional[str]:
    """Determine which section processed this group."""
    # Income checks
    if group.has_deposit_type():
        if group.any_name_contains("onesky"):
            return "income"
        if group.any_memo_contains("interest") or group.any_memo_contains("ped"):
            return "income"
    
    if group.any_memo_contains("transfer") and not group.any_memo_contains("elc"):
        return "income"
    
    # Advance/Settlement checks
    if group.any_memo_contains("settlement"):
        return "advance_settlement"
    if group.any_memo_contains("advance"):
        return "advance_settlement"
    
    # Nature check (non-manual)
    if any(r.nature_category and r.nature_category != "manual" for r in group.detail_rows):
        return "nature"
    
    return None

