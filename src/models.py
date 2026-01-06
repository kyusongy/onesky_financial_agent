"""
Data classes for transaction processing.

This module defines the core data structures for the financial reporting system:
- ReportSection: Enum for categorizing transactions
- TransactionEntry: Individual transaction split rows
- TransactionGroup: Grouped transaction with header info and entries
- ProcessingResult: Aggregated processing results
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from enum import Enum
import uuid


class ReportSection(Enum):
    """Report section categories for transaction assignment."""
    INCOME = "Income"
    ADVANCE_SETTLEMENT = "Advance_Settlement"
    NATURE = "Nature"
    MANUAL = "Manual"
    IGNORE = "Ignore"  # For "Transfer" out-legs or internal logic


# Bank identifier constants
BANK_USD = "29"
BANK_VND = "30"


@dataclass
class TransactionEntry:
    """
    Represents a single row within a transaction group (the splits).
    
    These are the child rows that follow the header/bank row in a transaction.
    """
    row_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    account_code: str = ""        # e.g., "71101VN", extracted from Account column
    account_name: str = ""        # Full account name
    original_memo: str = ""       # The memo for this specific row
    amount: float = 0.0           # The amount for this split
    original_row_index: int = 0   # Track original position for highlighting
    
    # Enrichment fields (filled during processing)
    nature_type: Optional[str] = None   # From Nature Lookup or Manual Logic
    is_manual_trigger: bool = False     # If account is 1250, 1500, etc.
    is_ignored: bool = False            # For the "Row 13" removal logic

    def get_account_number(self) -> str:
        """Return the account code."""
        return self.account_code

    def memo_contains(self, keyword: str, case_sensitive: bool = False) -> bool:
        """Check if memo contains a keyword."""
        if not self.original_memo:
            return False
        memo = self.original_memo
        if not case_sensitive:
            memo = memo.lower()
            keyword = keyword.lower()
        return keyword in memo


@dataclass
class TransactionGroup:
    """
    Represents the grouped transaction (The Parent).
    
    Contains header row information and a list of child entries (splits).
    """
    group_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    date: Optional[datetime] = None
    bank_identifier: str = BANK_VND  # "29" (USD) or "30" (VND)
    transaction_type: str = ""       # Deposit, Cheque Expense, Transfer
    payee_name: str = ""             # "Name" column
    bank_memo: str = ""              # Memo of the first row (Header)
    bank_amount: float = 0.0         # Amount of the first row (Total)
    currency: str = "VND"            # USD or VND
    exchange_rate: float = 1.0       # User input for USD rows
    original_row_index: int = 0      # Track original position for highlighting
    
    # The children rows (starts from the 2nd row of the raw group)
    entries: List[TransactionEntry] = field(default_factory=list)
    
    # State Management
    is_processed: bool = False
    assigned_section: Optional[ReportSection] = None
    
    @property
    def total_entries_count(self) -> int:
        """Get the number of child entries."""
        return len(self.entries)

    @property
    def active_entries(self) -> List[TransactionEntry]:
        """Get entries that are not ignored and have non-zero amounts."""
        return [e for e in self.entries if not e.is_ignored and e.amount]

    def get_net_amount_vnd(self) -> float:
        """Helper to get value in VND for reporting."""
        if self.bank_identifier == BANK_USD:
            return self.bank_amount * self.exchange_rate
        return self.bank_amount

    def get_converted_amount(self, ex_rate: Optional[float] = None) -> float:
        """
        Convert amount using exchange rate (divide for VND to USD).
        
        For USD bank transactions, divides by exchange rate.
        For VND bank transactions, returns amount as-is.
        """
        rate = ex_rate if ex_rate is not None else self.exchange_rate
        if self.bank_identifier == BANK_USD and rate > 0:
            return self.bank_amount / rate
        return self.bank_amount

    def is_deposit(self) -> bool:
        """Check if transaction type is deposit."""
        if not self.transaction_type:
            return False
        return self.transaction_type.lower().strip() == "deposit"

    def is_transfer(self) -> bool:
        """Check if transaction type is transfer."""
        if not self.transaction_type:
            return False
        return self.transaction_type.lower().strip() == "transfer"

    def memo_contains(self, keyword: str, case_sensitive: bool = False) -> bool:
        """Check if bank memo contains a keyword."""
        if not self.bank_memo:
            return False
        memo = self.bank_memo
        if not case_sensitive:
            memo = memo.lower()
            keyword = keyword.lower()
        return keyword in memo

    def name_contains(self, keyword: str, case_sensitive: bool = False) -> bool:
        """Check if payee name contains a keyword."""
        if not self.payee_name:
            return False
        name = self.payee_name
        if not case_sensitive:
            name = name.lower()
            keyword = keyword.lower()
        return keyword in name

    def any_memo_contains(self, keyword: str) -> bool:
        """Check if bank memo or any entry's memo contains keyword."""
        if self.memo_contains(keyword):
            return True
        return any(entry.memo_contains(keyword) for entry in self.entries)

    def any_name_contains(self, keyword: str) -> bool:
        """Check if payee name contains keyword."""
        return self.name_contains(keyword)


@dataclass
class ProcessingResult:
    """Result of transaction processing."""
    groups_by_bank: dict[str, list[TransactionGroup]] = field(default_factory=dict)
    
    # Income totals per bank
    income: dict[str, dict[str, float]] = field(default_factory=dict)
    
    # Advance/Settlement totals per bank
    advance_settlement: dict[str, dict[str, float]] = field(default_factory=dict)
    
    # Nature totals per bank
    nature_totals: dict[str, dict[str, float]] = field(default_factory=dict)
    
    # Groups requiring manual review
    manual_groups: list[TransactionGroup] = field(default_factory=list)
    
    # All processed groups (for marked transaction output)
    all_groups: list[TransactionGroup] = field(default_factory=list)
    
    # Exchange rates by date
    exchange_rates: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        # Initialize nested dicts for both bank types
        for bank in [BANK_USD, BANK_VND]:
            if bank not in self.groups_by_bank:
                self.groups_by_bank[bank] = []
            if bank not in self.income:
                self.income[bank] = {
                    "contribution": 0.0,
                    "fund_transfer": 0.0,
                    "fund_transfer_elc": 0.0,
                    "interest": 0.0,
                    "ped": 0.0,
                }
            if bank not in self.advance_settlement:
                self.advance_settlement[bank] = {
                    "advance": 0.0,
                    "settlement": 0.0,
                }
            if bank not in self.nature_totals:
                self.nature_totals[bank] = {
                    "org": 0.0,
                    "edu": 0.0,
                    "oper": 0.0,
                    "nutrition": 0.0,
                    "edu_infra": 0.0,
                    "manual": 0.0,
                }
    
    def get_income_total(self, bank_id: str) -> float:
        """Get total income for a bank type."""
        return sum(self.income.get(bank_id, {}).values())
    
    def get_expense_total(self, bank_id: str) -> float:
        """Get total expenses (by nature) for a bank type."""
        return sum(self.nature_totals.get(bank_id, {}).values())
    
    def get_advance_settlement_total(self, bank_id: str) -> float:
        """Get total advance/settlement for a bank type."""
        return sum(self.advance_settlement.get(bank_id, {}).values())
