"""
Transaction file parser for handling complex Excel structure.
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union
from datetime import datetime

from .models import TransactionRow, TransactionGroup, BankType


def parse_transaction_file(
    file_source: Union[str, Path, BinaryIO]
) -> tuple[list[TransactionGroup], bool]:
    """
    Parse Transaction.xlsx file and return grouped transactions.
    
    Args:
        file_source: Path to file or file-like object
        
    Returns:
        Tuple of (list of TransactionGroups, has_usd_transactions)
    """
    # Read Excel with header at row 3 (0-based)
    df = pd.read_excel(file_source, header=None, engine='openpyxl')
    
    # Find the actual data start (after header row)
    # Header row contains: Date, Transaction Type, No., Posting, Name, Memo/Description, Account, Amount
    header_row_idx = _find_header_row(df)
    
    # Process rows starting after header
    groups = []
    current_bank_type: Optional[BankType] = None
    current_group_rows: list[TransactionRow] = []
    current_date: Optional[datetime] = None
    
    for idx in range(header_row_idx + 1, len(df)):
        row = df.iloc[idx]
        
        # Check for bank section markers
        bank_marker = _check_bank_marker(row)
        if bank_marker:
            # Save current group if exists
            if current_group_rows:
                groups.append(_create_group(current_group_rows, current_bank_type, current_date))
                current_group_rows = []
            current_bank_type = bank_marker
            continue
        
        # Check for empty row (group separator)
        if _is_empty_row(row):
            if current_group_rows:
                groups.append(_create_group(current_group_rows, current_bank_type, current_date))
                current_group_rows = []
                current_date = None
            continue
        
        # Parse transaction row
        tx_row = _parse_row(row, idx)
        
        # Update date if this row has a date
        if tx_row.date:
            current_date = tx_row.date
        
        current_group_rows.append(tx_row)
    
    # Don't forget the last group
    if current_group_rows:
        groups.append(_create_group(current_group_rows, current_bank_type, current_date))
    
    # Check if there are USD transactions
    has_usd = any(g.bank_type == BankType.USD for g in groups)
    
    return groups, has_usd


def _find_header_row(df: pd.DataFrame) -> int:
    """Find the row index containing column headers."""
    for idx in range(min(10, len(df))):  # Check first 10 rows
        row = df.iloc[idx]
        # Look for "Date" in the row
        for val in row.values:
            if isinstance(val, str) and val.strip().lower() == "date":
                return idx
    return 3  # Default to row 3 if not found


def _check_bank_marker(row: pd.Series) -> Optional[BankType]:
    """Check if row is a bank section marker."""
    first_val = row.iloc[0]
    if pd.isna(first_val):
        return None
    
    val_str = str(first_val).lower().strip()
    
    if "29" in val_str and "usd" in val_str:
        return BankType.USD
    if "30" in val_str and "vnd" in val_str:
        return BankType.VND
    
    return None


def _is_empty_row(row: pd.Series) -> bool:
    """Check if row is empty (all NaN or empty strings)."""
    for val in row.values:
        if pd.notna(val) and str(val).strip():
            return False
    return True


def _parse_row(row: pd.Series, original_idx: int) -> TransactionRow:
    """Parse a single transaction row."""
    # Column indices (based on observed structure)
    # 0: Label/empty, 1: Date, 2: Transaction Type, 3: No., 4: Posting,
    # 5: Name, 6: Memo/Description, 7: Account, 8: Amount
    
    date_val = _parse_date(row.iloc[1]) if len(row) > 1 else None
    tx_type = _safe_str(row.iloc[2]) if len(row) > 2 else None
    name = _safe_str(row.iloc[5]) if len(row) > 5 else None
    memo = _safe_str(row.iloc[6]) if len(row) > 6 else None
    account = _safe_str(row.iloc[7]) if len(row) > 7 else None
    amount = _parse_amount(row.iloc[8]) if len(row) > 8 else None
    
    return TransactionRow(
        date=date_val,
        transaction_type=tx_type,
        name=name,
        memo=memo,
        account=account,
        amount=amount,
        original_row_index=original_idx,
    )


def _parse_date(val) -> Optional[datetime]:
    """Parse date value."""
    if pd.isna(val):
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    try:
        return pd.to_datetime(val).to_pydatetime()
    except Exception:
        return None


def _parse_amount(val) -> Optional[float]:
    """Parse amount value."""
    if pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_str(val) -> Optional[str]:
    """Convert value to string safely."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def _create_group(
    rows: list[TransactionRow],
    bank_type: Optional[BankType],
    date: Optional[datetime]
) -> TransactionGroup:
    """Create a TransactionGroup from rows."""
    # Default to VND if bank type not determined
    if bank_type is None:
        # Try to infer from account column
        for row in rows:
            if row.account:
                account_str = str(row.account).lower()
                if "usd" in account_str or "29 bank" in account_str:
                    bank_type = BankType.USD
                    break
                if "vnd" in account_str or "30 bank" in account_str:
                    bank_type = BankType.VND
                    break
        if bank_type is None:
            bank_type = BankType.VND  # Default
    
    return TransactionGroup(
        date=date,
        bank_type=bank_type,
        rows=rows,
    )

