"""
Transaction processor for grouping, splitting, and capital removal.
"""
from typing import Optional
from copy import deepcopy

from .models import TransactionRow, TransactionGroup, BankType


def process_transactions(
    groups: list[TransactionGroup],
    exchange_rate: Optional[float] = None
) -> dict[BankType, list[TransactionGroup]]:
    """
    Process transaction groups:
    1. Split transfer groups (containing both banks) into separate groups
    2. Remove capital rows and their opposite-amount pairs
    
    Args:
        groups: List of parsed transaction groups
        exchange_rate: Exchange rate for USD transactions (VND to USD)
        
    Returns:
        Dictionary mapping BankType to list of processed groups
    """
    processed_groups: dict[BankType, list[TransactionGroup]] = {
        BankType.USD: [],
        BankType.VND: [],
    }
    
    for group in groups:
        # Step 1: Split transfer groups if needed
        split_groups = _split_transfer_group(group)
        
        for split_group in split_groups:
            # Step 2: Remove capital rows
            cleaned_group = _remove_capital_rows(split_group)
            
            # Only add non-empty groups
            if cleaned_group.rows:
                processed_groups[cleaned_group.bank_type].append(cleaned_group)
    
    return processed_groups


def _split_transfer_group(group: TransactionGroup) -> list[TransactionGroup]:
    """
    Split a group if it's a transfer containing both USD and VND bank rows.
    
    For transfer transactions, both banks appear in the same group.
    We need to split them into separate groups based on the bank type.
    """
    # Check if this is a transfer group by looking at transaction type
    is_transfer = any(row.is_transfer() for row in group.rows)
    
    if not is_transfer:
        return [group]
    
    # Check if group contains both bank types
    usd_rows: list[TransactionRow] = []
    vnd_rows: list[TransactionRow] = []
    
    for row in group.rows:
        bank = _determine_row_bank(row)
        if bank == BankType.USD:
            usd_rows.append(deepcopy(row))
        elif bank == BankType.VND:
            vnd_rows.append(deepcopy(row))
        else:
            # If can't determine, keep with original bank type
            if group.bank_type == BankType.USD:
                usd_rows.append(deepcopy(row))
            else:
                vnd_rows.append(deepcopy(row))
    
    result = []
    
    if usd_rows:
        result.append(TransactionGroup(
            date=group.date,
            bank_type=BankType.USD,
            rows=usd_rows,
        ))
    
    if vnd_rows:
        result.append(TransactionGroup(
            date=group.date,
            bank_type=BankType.VND,
            rows=vnd_rows,
        ))
    
    # If no split occurred, return original
    if not result:
        return [group]
    
    return result


def _determine_row_bank(row: TransactionRow) -> Optional[BankType]:
    """Determine bank type from a row's account column."""
    if not row.account:
        return None
    
    account_str = str(row.account).lower()
    
    # Check for explicit bank markers
    if "29 bank" in account_str or "vietnam (usd)" in account_str:
        return BankType.USD
    if "30 bank" in account_str or "vietnam (vnd)" in account_str:
        return BankType.VND
    
    # Check for USD/VND keywords
    if "usd" in account_str:
        return BankType.USD
    if "vnd" in account_str:
        return BankType.VND
    
    return None


def _remove_capital_rows(group: TransactionGroup) -> TransactionGroup:
    """
    Remove capital rows and their offset pairs.
    
    Capital row identification:
    - Memo contains 'capital' AND account code starts with '13' (e.g., 1310VN)
    
    For each capital row found:
    1. Remove the capital row itself
    2. Remove the row immediately before it (the offset row)
    """
    rows = deepcopy(group.rows)
    rows_to_remove: set[int] = set()
    
    # Find capital rows: memo contains "capital" AND account starts with "13"
    for i, row in enumerate(rows):
        if row.memo_contains("capital"):
            # Check if account code starts with "13"
            account_num = row.get_account_number()
            if account_num and account_num.lower().startswith("13"):
                # This is the capital row to remove
                rows_to_remove.add(i)
                
                # Also remove the row immediately before it (the offset row)
                if i > 0:
                    rows_to_remove.add(i - 1)
    
    # Create new group with remaining rows
    remaining_rows = [row for i, row in enumerate(rows) if i not in rows_to_remove]
    
    return TransactionGroup(
        date=group.date,
        bank_type=group.bank_type,
        rows=remaining_rows,
        requires_manual_review=group.requires_manual_review,
    )


def has_usd_transactions(groups: list[TransactionGroup]) -> bool:
    """Check if any group is for USD bank."""
    return any(g.bank_type == BankType.USD for g in groups)

