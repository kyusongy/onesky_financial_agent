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
    ) -> tuple[dict[str, dict[str, float]], list[TransactionGroup]]:
        """
        Process manual input groups and calculate nature totals.
        
        Returns:
            Tuple of (nature_totals by bank, groups still needing manual review)
        """
        nature_totals: dict[str, dict[str, float]] = {
            BANK_USD: self._init_totals(),
            BANK_VND: self._init_totals(),
        }
        still_manual: list[TransactionGroup] = []
        
        for group in manual_groups:
            if group.is_processed:
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
                group.assigned_section = ReportSection.MANUAL
                for nature_key, amount in result["amounts"].items():
                    if nature_key in nature_totals[group.bank_identifier]:
                        nature_totals[group.bank_identifier][nature_key] += amount
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
        for entry in group.entries:
            acct_type = get_account_type(entry.account_code)
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
        
        For 1-entry groups: look up nature by amount, enter absolute bank value.
        For >1 entries with 1500: defer to 1500 logic.
        For >1 entries without 1500: process each entry individually.
        """
        result = {"processed": False, "amounts": {}}
        active_entries = group.active_entries
        num_entries = len(active_entries)
        
        if num_entries == 1:
            entry = active_entries[0]
            if entry.amount:
                nature = self.lookup_by_amount(account_type, entry.amount)
                if nature:
                    result["amounts"][nature] = abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
                    result["processed"] = True
                    entry.nature_type = nature
        
        elif num_entries > 1:
            # Check if any entry is 1500
            has_1500 = any(get_account_type(e.account_code) == "1500" for e in active_entries)
            
            if has_1500:
                return self._process_1500(group, ex_rate)
            
            # Process each entry individually
            for entry in active_entries:
                if entry.amount:
                    nature = self.lookup_by_amount(account_type, entry.amount)
                    if nature:
                        amount = abs(convert_amount(entry.amount, group.bank_identifier, ex_rate))
                        result["amounts"][nature] = result["amounts"].get(nature, 0.0) + amount
                        entry.nature_type = nature
                        result["processed"] = True
        
        return result
    
    def _process_1500(self, group: TransactionGroup, ex_rate: float) -> dict:
        """
        Process 1500 groups.
        
        1. Negative 1500: look up nature, enter ABSOLUTE BANK AMOUNT
        2. Positive 1500: if memo contains PIT/SI/HI → Org, else use previous entry's nature
        3. For multi-entry: enter each entry's amount by nature, subtract positive 1500
        """
        result = {"processed": False, "amounts": {}}
        bank_amount = abs(convert_amount(group.bank_amount, group.bank_identifier, ex_rate))
        active_entries = group.active_entries
        
        # First pass: determine nature for non-1500 entries
        prev_nature = None
        for entry in active_entries:
            if get_account_type(entry.account_code) != "1500" and entry.amount:
                for acct_type in ("1250", "1230", "1252"):
                    nature = self.lookup_by_amount(acct_type, entry.amount)
                    if nature:
                        entry.nature_type = nature
                        prev_nature = nature
                        break
        
        # Second pass: process 1500 entries
        negative_1500_entries = []
        positive_1500_by_nature: dict[str, float] = {}
        
        for entry in active_entries:
            is_1500 = get_account_type(entry.account_code) == "1500"
            if not is_1500 or not entry.amount:
                continue
            
            if entry.amount < 0:
                nature = self.lookup_by_amount("1500", entry.amount)
                if nature:
                    entry.nature_type = nature
                    prev_nature = nature
                    negative_1500_entries.append(entry)
            elif entry.amount > 0:
                if any(entry.memo_contains(kw) for kw in ("pit", "si", "hi")):
                    entry.nature_type = "org"
                elif prev_nature:
                    entry.nature_type = prev_nature
                
                if entry.nature_type:
                    amt = abs(convert_amount(entry.amount, group.bank_identifier, ex_rate))
                    positive_1500_by_nature[entry.nature_type] = \
                        positive_1500_by_nature.get(entry.nature_type, 0.0) + amt
        
        # Add negative 1500 amounts using bank_amount
        for entry in negative_1500_entries:
            if entry.nature_type:
                result["amounts"][entry.nature_type] = \
                    result["amounts"].get(entry.nature_type, 0.0) + bank_amount
                result["processed"] = True
        
        # Add non-1500 entry amounts
        for entry in active_entries:
            if not entry.amount or not entry.nature_type:
                continue
            is_1500 = get_account_type(entry.account_code) == "1500"
            if is_1500:
                continue
            
            amount = abs(convert_amount(entry.amount, group.bank_identifier, ex_rate))
            result["amounts"][entry.nature_type] = \
                result["amounts"].get(entry.nature_type, 0.0) + amount
            result["processed"] = True
        
        # Subtract positive 1500 amounts
        for nature, subtract_amt in positive_1500_by_nature.items():
            if nature in result["amounts"]:
                result["amounts"][nature] -= subtract_amt
        
        if result["amounts"]:
            result["processed"] = True
        
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
        if group.any_memo_contains("interest") or group.any_memo_contains("ped"):
            return ReportSection.INCOME
    
    if group.any_memo_contains("transfer"):
        return ReportSection.INCOME
    
    # Advance/Settlement checks
    if group.any_memo_contains("settlement"):
        return ReportSection.ADVANCE_SETTLEMENT
    if group.any_memo_contains("advance"):
        return ReportSection.ADVANCE_SETTLEMENT
    
    # Nature check (non-manual)
    active_entries = group.active_entries
    if any(e.nature_type and e.nature_type != "manual" and not e.is_manual_trigger for e in active_entries):
        return ReportSection.NATURE
    
    return None
