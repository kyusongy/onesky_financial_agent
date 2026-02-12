"""
Transaction processor for grouping, splitting, and capital removal.

Handles:
1. Splitting transfer groups (containing both banks) into separate groups
2. Removing capital rows (Row 13 cleaning rule)
"""
from typing import Optional
from copy import deepcopy
import uuid

from .models import TransactionEntry, TransactionGroup, BANK_USD, BANK_VND, BANK_34


def process_transactions(
    groups: list[TransactionGroup],
    exchange_rate: Optional[float] = None
) -> dict[str, list[TransactionGroup]]:
    """
    Process transaction groups:
    1. Split transfer groups (containing both banks) into separate groups
    2. Remove capital rows and their opposite-amount pairs
    
    Args:
        groups: List of parsed transaction groups
        exchange_rate: Exchange rate for USD transactions (VND to USD)
        
    Returns:
        Dictionary mapping bank_identifier to list of processed groups
    """
    processed_groups: dict[str, list[TransactionGroup]] = {
        BANK_USD: [],
        BANK_VND: [],
        BANK_34: [],
    }
    
    for group in groups:
        # Step 1: Split transfer groups if needed
        split_groups = _split_transfer_group(group)
        
        for split_group in split_groups:
            # Step 2: Remove capital rows (mark as ignored)
            cleaned_group = _remove_capital_rows(split_group)

            processed_groups[cleaned_group.bank_identifier].append(cleaned_group)
    
    return processed_groups


def _is_bank_entry(entry: TransactionEntry) -> bool:
    """Check if this entry is a bank account row (should be a header, not entry)."""
    if not entry.account_name:
        return False
    account_lower = entry.account_name.lower()
    return "bank vn" in account_lower or "29 bank" in account_lower or "30 bank" in account_lower or "34 bank" in account_lower


def _split_transfer_group(group: TransactionGroup) -> list[TransactionGroup]:
    """
    Split a group if it's a transfer containing both USD and VND bank rows.
    
    For transfer transactions, the raw data contains two bank account rows:
    - First row: One bank (e.g., 29 Bank) - becomes header with bank_amount
    - Second row: Other bank (e.g., 30 Bank) - stored as entry but should be split
    
    This function:
    1. Detects entries that are actually bank rows
    2. Creates separate groups for each bank, with proper bank_amount
    3. Non-bank entries are assigned to their respective bank groups
    """
    # Check if this is a transfer group
    if not group.is_transfer():
        return [group]
    
    # Find bank entries (entries that are actually bank rows)
    bank_entries: list[TransactionEntry] = []
    non_bank_entries: list[TransactionEntry] = []
    
    for entry in group.entries:
        if _is_bank_entry(entry):
            bank_entries.append(entry)
        else:
            non_bank_entries.append(entry)
    
    # If no bank entries found, no split needed
    if not bank_entries:
        return [group]
    
    # Create groups for each bank
    # The original header (group's bank_amount) is for the original bank_identifier
    # Each bank entry becomes a new group with its own bank_amount
    
    result = []
    
    # Original group (header row's bank)
    original_bank = group.bank_identifier
    original_non_bank = [deepcopy(e) for e in non_bank_entries 
                         if _determine_entry_bank(e) == original_bank or _determine_entry_bank(e) is None]
    
    # Filter non-bank entries to only those that don't belong to a specific other bank
    original_entries = []
    for e in non_bank_entries:
        entry_bank = _determine_entry_bank(e)
        if entry_bank is None or entry_bank == original_bank:
            original_entries.append(deepcopy(e))
    
    result.append(TransactionGroup(
        group_id=str(uuid.uuid4()),
        date=group.date,
        bank_identifier=original_bank,
        transaction_type=group.transaction_type,
        payee_name=group.payee_name,
        bank_memo=group.bank_memo,
        bank_amount=group.bank_amount,
        currency="USD" if original_bank == BANK_USD else "VND",
        exchange_rate=group.exchange_rate,
        original_row_index=group.original_row_index,
        entries=original_entries,
    ))
    
    # Create new groups for each bank entry
    for bank_entry in bank_entries:
        entry_bank = _determine_entry_bank(bank_entry)
        if entry_bank is None:
            continue
        
        # Find non-bank entries that belong to this bank
        bank_non_bank_entries = []
        for e in non_bank_entries:
            e_bank = _determine_entry_bank(e)
            if e_bank == entry_bank:
                bank_non_bank_entries.append(deepcopy(e))
        
        result.append(TransactionGroup(
            group_id=str(uuid.uuid4()),
            date=group.date,
            bank_identifier=entry_bank,
            transaction_type=group.transaction_type,
            payee_name=group.payee_name,
            bank_memo=bank_entry.original_memo or group.bank_memo,
            bank_amount=bank_entry.amount,  # Use the bank entry's amount
            currency="USD" if entry_bank == BANK_USD else "VND",
            exchange_rate=group.exchange_rate,
            original_row_index=bank_entry.original_row_index,
            entries=bank_non_bank_entries,
        ))
    
    return result if result else [group]


def _determine_entry_bank(entry: TransactionEntry) -> Optional[str]:
    """Determine bank type from an entry's account column."""
    if not entry.account_name:
        return None

    account_str = entry.account_name.lower()

    # Check for explicit bank markers
    if "29 bank" in account_str or "vietnam (usd)" in account_str:
        return BANK_USD
    if "30 bank" in account_str or "vietnam (vnd)" in account_str:
        return BANK_VND
    if "34 bank" in account_str:
        return BANK_34

    # Check for USD/VND keywords in account code
    if "usd" in account_str:
        return BANK_USD
    if "vnd" in account_str:
        return BANK_VND

    return None


def _remove_capital_rows(group: TransactionGroup) -> TransactionGroup:
    """
    Remove capital rows and their offset pairs by marking them as ignored.

    Capital row identification:
    - Account code starts with '13' (e.g., 1310VN, 1311VN)

    For each capital row found:
    1. Mark the capital row as is_ignored
    2. Mark the row immediately before it as is_ignored
    """
    entries = group.entries
    indices_to_ignore: set[int] = set()

    # Find capital rows: account starts with "13"
    for i, entry in enumerate(entries):
        account_num = entry.get_account_number()
        if account_num and account_num.lower().startswith("13"):
            # Mark this row as ignored
            indices_to_ignore.add(i)

            # Also mark the row immediately before it (the offset row)
            if i > 0:
                indices_to_ignore.add(i - 1)

    # Mark entries as ignored
    for i in indices_to_ignore:
        if i < len(entries):
            entries[i].is_ignored = True

    return group
