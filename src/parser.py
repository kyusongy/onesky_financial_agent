"""
Transaction file parser for handling complex Excel structure.

Parses Transaction.xlsx and creates TransactionGroup objects with
header information extracted and child rows as TransactionEntry objects.
"""
import pandas as pd
from pathlib import Path
from typing import Optional, BinaryIO, Union
from datetime import datetime
import uuid

from .models import TransactionEntry, TransactionGroup, BANK_USD, BANK_VND, BANK_34


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
    header_row_idx = _find_header_row(df)
    
    # Process rows starting after header
    groups = []
    current_bank_id: Optional[str] = None
    current_group_rows: list[dict] = []
    current_date: Optional[datetime] = None
    
    for idx in range(header_row_idx + 1, len(df)):
        row = df.iloc[idx]

        # Check for bank section markers
        bank_marker = _check_bank_marker(row)
        if bank_marker:
            # Save current group if exists
            if current_group_rows and current_bank_id in (BANK_USD, BANK_VND, BANK_34):
                groups.append(_create_group(current_group_rows, current_bank_id, current_date))
            current_group_rows = []
            current_bank_id = bank_marker  # Could be "OTHER" - groups under this will be skipped
            continue

        # Skip rows under "OTHER" banks
        if current_bank_id == "OTHER":
            continue

        # Check for empty row (group separator)
        if _is_empty_row(row):
            if current_group_rows and current_bank_id in (BANK_USD, BANK_VND, BANK_34):
                groups.append(_create_group(current_group_rows, current_bank_id, current_date))
            current_group_rows = []
            current_date = None
            continue

        # Parse transaction row as dict
        row_data = _parse_row(row, idx)

        # Update date if this row has a date
        if row_data["date"]:
            current_date = row_data["date"]

        current_group_rows.append(row_data)

    # Don't forget the last group (only if it's a valid 29/30 bank)
    if current_group_rows and current_bank_id in (BANK_USD, BANK_VND, BANK_34):
        groups.append(_create_group(current_group_rows, current_bank_id, current_date))
    
    # Check if there are USD transactions
    has_usd = any(g.bank_identifier == BANK_USD for g in groups)
    
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


def _check_bank_marker(row: pd.Series) -> Optional[str]:
    """
    Check if row is a bank section marker.

    Returns:
        BANK_USD for "29 USD" marker
        BANK_VND for "30 VND" marker
        "OTHER" for other bank markers - should be ignored
        None if not a bank marker
    """
    first_val = row.iloc[0]
    if pd.isna(first_val):
        return None

    val_str = str(first_val).lower().strip()

    # Check for 29 USD bank
    if "29" in val_str and "usd" in val_str:
        return BANK_USD
    # Check for 30 VND bank
    if "30" in val_str and "vnd" in val_str:
        return BANK_VND
    # Check for 34 VND bank
    if "34" in val_str and ("vnd" in val_str or "vietnam" in val_str or "bank" in val_str or "elc" in val_str):
        return BANK_34
    # Check for other bank markers (any number + currency/bank keywords) - mark as OTHER to ignore
    # Pattern: contains a number and bank-related keywords
    import re
    if re.search(r'\d+', val_str) and any(kw in val_str for kw in ("vnd", "usd", "vietnam", "bank", "elc")):
        return "OTHER"

    return None


def _is_empty_row(row: pd.Series) -> bool:
    """Check if row is empty (all NaN or empty strings)."""
    for val in row.values:
        if pd.notna(val) and str(val).strip():
            return False
    return True


def _parse_row(row: pd.Series, original_idx: int) -> dict:
    """Parse a single transaction row into a dictionary."""
    # Column indices (based on observed structure)
    # 0: Label/empty, 1: Date, 2: Transaction Type, 3: No., 4: Posting,
    # 5: Name, 6: Memo/Description, 7: Account, 8: Amount
    
    date_val = _parse_date(row.iloc[1]) if len(row) > 1 else None
    tx_type = _safe_str(row.iloc[2]) if len(row) > 2 else None
    name = _safe_str(row.iloc[5]) if len(row) > 5 else None
    memo = _safe_str(row.iloc[6]) if len(row) > 6 else None
    account = _safe_str(row.iloc[7]) if len(row) > 7 else None
    amount = _parse_amount(row.iloc[8]) if len(row) > 8 else None
    
    return {
        "date": date_val,
        "transaction_type": tx_type,
        "name": name,
        "memo": memo,
        "account": account,
        "amount": amount,
        "original_row_index": original_idx,
    }


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


def _extract_account_code(account_str: Optional[str]) -> str:
    """Extract account number from account string (e.g., '71101VN' from '71101VN Contributions:...')."""
    if not account_str:
        return ""
    # Account number is the first part before any space or colon
    account_str = account_str.strip()
    for i, char in enumerate(account_str):
        if char in (' ', ':'):
            return account_str[:i]
    return account_str


def _determine_currency(account_str: Optional[str]) -> str:
    """Determine currency from account string."""
    if not account_str:
        return "VND"
    account_lower = account_str.lower()
    if "usd" in account_lower:
        return "USD"
    return "VND"


def _create_group(
    rows: list[dict],
    bank_id: Optional[str],
    date: Optional[datetime]
) -> TransactionGroup:
    """
    Create a TransactionGroup from parsed rows.
    
    The first row becomes the header (bank_amount, bank_memo, etc.)
    Subsequent rows become TransactionEntry objects.
    """
    if not rows:
        return TransactionGroup(bank_identifier=bank_id or BANK_VND, date=date)
    
    # First row is the header/bank row
    header = rows[0]
    
    # Determine bank identifier if not already set
    if bank_id is None:
        # Try to infer from account column
        for row_data in rows:
            account = row_data.get("account")
            if account:
                account_str = str(account).lower()
                if "usd" in account_str or "29 bank" in account_str:
                    bank_id = BANK_USD
                    break
                if "vnd" in account_str or "30 bank" in account_str:
                    bank_id = BANK_VND
                    break
        if bank_id is None:
            bank_id = BANK_VND  # Default
    
    # Determine currency
    currency = "USD" if bank_id == BANK_USD else "VND"
    
    # Create entries from subsequent rows (skip first/header row)
    entries = []
    for row_data in rows[1:]:
        account_str = row_data.get("account") or ""
        entry = TransactionEntry(
            row_id=str(uuid.uuid4()),
            account_code=_extract_account_code(account_str),
            account_name=account_str,
            original_memo=row_data.get("memo") or "",
            amount=row_data.get("amount") or 0.0,
            original_row_index=row_data.get("original_row_index", 0),
        )
        entries.append(entry)
    
    return TransactionGroup(
        group_id=str(uuid.uuid4()),
        date=date or header.get("date"),
        bank_identifier=bank_id,
        transaction_type=header.get("transaction_type") or "",
        payee_name=header.get("name") or "",
        bank_memo=header.get("memo") or "",
        bank_amount=header.get("amount") or 0.0,
        currency=currency,
        original_row_index=header.get("original_row_index", 0),
        entries=entries,
    )
