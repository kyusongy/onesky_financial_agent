"""
Data classes for transaction processing.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class BankType(Enum):
    """Bank type identifier."""
    USD = 29  # USD Bank
    VND = 30  # VND Bank


@dataclass
class TransactionRow:
    """Represents a single transaction row."""
    date: Optional[datetime]
    transaction_type: Optional[str]
    name: Optional[str]
    memo: Optional[str]
    account: Optional[str]
    amount: Optional[float]
    # Derived fields
    nature_category: Optional[str] = None
    requires_manual_review: bool = False
    original_row_index: int = 0  # Track original position for highlighting

    def get_account_number(self) -> Optional[str]:
        """Extract account number from account string (e.g., '71101VN' from '71101VN Contributions:...')."""
        if not self.account:
            return None
        # Account number is the first part before any space or colon
        account_str = str(self.account).strip()
        # Find the first space or colon
        for i, char in enumerate(account_str):
            if char in (' ', ':'):
                return account_str[:i]
        return account_str

    def memo_contains(self, keyword: str, case_sensitive: bool = False) -> bool:
        """Check if memo contains a keyword."""
        if not self.memo:
            return False
        memo = str(self.memo)
        if not case_sensitive:
            memo = memo.lower()
            keyword = keyword.lower()
        return keyword in memo

    def name_contains(self, keyword: str, case_sensitive: bool = False) -> bool:
        """Check if name contains a keyword."""
        if not self.name:
            return False
        name = str(self.name)
        if not case_sensitive:
            name = name.lower()
            keyword = keyword.lower()
        return keyword in name

    def is_deposit(self) -> bool:
        """Check if transaction type is deposit."""
        if not self.transaction_type:
            return False
        return str(self.transaction_type).lower().strip() == "deposit"

    def is_transfer(self) -> bool:
        """Check if transaction type is transfer."""
        if not self.transaction_type:
            return False
        return str(self.transaction_type).lower().strip() == "transfer"


@dataclass
class TransactionGroup:
    """Collection of transaction rows grouped by date."""
    date: Optional[datetime]
    bank_type: BankType
    rows: list[TransactionRow] = field(default_factory=list)
    requires_manual_review: bool = False
    
    @property
    def first_row(self) -> Optional[TransactionRow]:
        """Get the first row (usually the bank account row)."""
        return self.rows[0] if self.rows else None
    
    @property
    def detail_rows(self) -> list[TransactionRow]:
        """Get all rows except the first (detail rows)."""
        return self.rows[1:] if len(self.rows) > 1 else []
    
    @property
    def first_amount(self) -> float:
        """Get the amount from the first row."""
        if self.first_row and self.first_row.amount is not None:
            return self.first_row.amount
        return 0.0

    def get_total_amount(self) -> float:
        """Sum all amounts in the group."""
        return sum(row.amount or 0 for row in self.rows)


@dataclass
class ProcessingResult:
    """Result of transaction processing."""
    groups_by_bank: dict[BankType, list[TransactionGroup]] = field(default_factory=dict)
    
    # Income totals per bank
    income: dict[BankType, dict[str, float]] = field(default_factory=dict)
    
    # Advance/Settlement totals per bank
    advance_settlement: dict[BankType, dict[str, float]] = field(default_factory=dict)
    
    # Nature totals per bank
    nature_totals: dict[BankType, dict[str, float]] = field(default_factory=dict)
    
    # Groups requiring manual review
    manual_groups: list[TransactionGroup] = field(default_factory=list)
    
    # Exchange rate used (if USD transactions present)
    exchange_rate: Optional[float] = None

    def __post_init__(self):
        # Initialize nested dicts for both bank types
        for bank in BankType:
            if bank not in self.groups_by_bank:
                self.groups_by_bank[bank] = []
            if bank not in self.income:
                self.income[bank] = {
                    "contribution": 0.0,
                    "fund_transfer": 0.0,
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

