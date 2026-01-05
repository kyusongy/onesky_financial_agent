"""
Report generator for filling the output template.
"""
import pandas as pd
from openpyxl import load_workbook, Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from pathlib import Path
from typing import Optional, BinaryIO, Union
from io import BytesIO
from datetime import datetime

from .models import TransactionGroup, TransactionRow, ProcessingResult, BankType
from config.mappings import (
    DEFAULT_OUTPUT_TEMPLATE,
    TEMPLATE_COL_VND,
    TEMPLATE_COL_USD,
    TEMPLATE_ROWS,
    NATURE_DISPLAY_MAP,
)


# Yellow highlight for manual review
HIGHLIGHT_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)


class ReportGenerator:
    """Generates filled report from processing results."""
    
    def __init__(self, template_source: Optional[Union[str, Path, BinaryIO]] = None):
        """
        Initialize with output template.
        
        Args:
            template_source: Path to template file or file-like object.
                            Uses default if not provided.
        """
        self.template_source = template_source or DEFAULT_OUTPUT_TEMPLATE
    
    def generate_report(self, result: ProcessingResult) -> bytes:
        """
        Generate filled report Excel file.
        
        Args:
            result: Processing result containing all calculated values
            
        Returns:
            Bytes of the filled Excel file
        """
        # Load template
        wb = load_workbook(self.template_source)
        ws = wb.active
        
        # Fill income section
        self._fill_income(ws, result)
        
        # Fill advance/settlement section
        self._fill_advance_settlement(ws, result)
        
        # Fill nature section
        self._fill_nature(ws, result)
        
        # Save to bytes
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output.read()
    
    def _fill_income(self, ws, result: ProcessingResult) -> None:
        """Fill income section values with totals."""
        for bank_type, values in result.income.items():
            col = self._get_column(bank_type)
            
            # Contribution
            self._set_cell(ws, TEMPLATE_ROWS["contribution"], col, values.get("contribution", 0))
            
            # Fund transfer
            self._set_cell(ws, TEMPLATE_ROWS["fund_transfer"], col, values.get("fund_transfer", 0))
            
            # Interest
            self._set_cell(ws, TEMPLATE_ROWS["interest"], col, values.get("interest", 0))
            
            # PED
            self._set_cell(ws, TEMPLATE_ROWS["ped"], col, values.get("ped", 0))
            
            # Total for income section
            income_total = sum(values.values())
            if "income_total" in TEMPLATE_ROWS:
                self._set_cell(ws, TEMPLATE_ROWS["income_total"], col, income_total)
    
    def _fill_advance_settlement(self, ws, result: ProcessingResult) -> None:
        """Fill advance/settlement section values with totals."""
        for bank_type, values in result.advance_settlement.items():
            col = self._get_column(bank_type)
            
            # Advance
            self._set_cell(ws, TEMPLATE_ROWS["advance"], col, values.get("advance", 0))
            
            # Settlement
            self._set_cell(ws, TEMPLATE_ROWS["settlement"], col, values.get("settlement", 0))
            
            # Total for advance/settlement section
            total = sum(values.values())
            if "advance_settlement_total" in TEMPLATE_ROWS:
                self._set_cell(ws, TEMPLATE_ROWS["advance_settlement_total"], col, total)
    
    def _fill_nature(self, ws, result: ProcessingResult) -> None:
        """Fill expenditure by nature section values with totals."""
        for bank_type, values in result.nature_totals.items():
            col = self._get_column(bank_type)
            
            # Org
            self._set_cell(ws, TEMPLATE_ROWS["org"], col, values.get("org", 0))
            
            # Edu
            self._set_cell(ws, TEMPLATE_ROWS["edu"], col, values.get("edu", 0))
            
            # Oper
            self._set_cell(ws, TEMPLATE_ROWS["oper"], col, values.get("oper", 0))
            
            # Nutrition
            self._set_cell(ws, TEMPLATE_ROWS["nutrition"], col, values.get("nutrition", 0))
            
            # Edu Infra
            self._set_cell(ws, TEMPLATE_ROWS["edu_infra"], col, values.get("edu_infra", 0))
            
            # Total for expense section (excluding manual)
            expense_total = sum(v for k, v in values.items() if k != "manual")
            if "expense_total" in TEMPLATE_ROWS:
                self._set_cell(ws, TEMPLATE_ROWS["expense_total"], col, expense_total)
            
            # Manual
            self._set_cell(ws, TEMPLATE_ROWS["manual"], col, values.get("manual", 0))
    
    def _get_column(self, bank_type: BankType) -> int:
        """Get column index for bank type (1-based for openpyxl)."""
        if bank_type == BankType.VND:
            return TEMPLATE_COL_VND + 1  # Column D (4)
        return TEMPLATE_COL_USD + 1  # Column E (5)
    
    def _set_cell(self, ws, row: int, col: int, value: float) -> None:
        """Set cell value if non-zero."""
        if value != 0:
            ws.cell(row=row + 1, column=col, value=value)  # +1 because openpyxl is 1-based


def generate_marked_transactions(
    original_file: Union[str, Path, BinaryIO],
    all_groups: list[TransactionGroup],
    exchange_rates: dict[str, float],
    nature_mapper=None
) -> bytes:
    """
    Generate a processed transaction file with additional columns:
    - Nature category for each row
    - Exchange rate (for USD transactions)
    - Amount in corresponding currency (VND for VND bank, USD for USD bank)
    
    Args:
        original_file: Path or file-like object of original transaction file
        all_groups: List of all processed transaction groups
        exchange_rates: Dictionary of date string to exchange rate
        nature_mapper: NatureMapper instance for looking up nature categories
        
    Returns:
        Bytes of the enhanced Excel file
    """
    wb = load_workbook(original_file)
    ws = wb.active
    
    # Find the last column and add new headers
    max_col = ws.max_column
    
    # Add new column headers (find header row first)
    header_row = 1
    for row in range(1, min(10, ws.max_row + 1)):
        cell_val = ws.cell(row=row, column=2).value
        if cell_val and str(cell_val).lower() == "date":
            header_row = row
            break
    
    # New columns
    col_exchange_rate = max_col + 1
    col_bank_label = max_col + 2
    col_month = max_col + 3
    col_account_num = max_col + 4
    col_nature = max_col + 5
    col_converted_amount = max_col + 6
    col_assigned_to = max_col + 7
    
    # Set headers
    headers = [
        ("Exchange Rate", col_exchange_rate),
        ("Bank", col_bank_label),
        ("Month", col_month),
        ("Account No.", col_account_num),
        ("Nature", col_nature),
        ("Amount (Currency)", col_converted_amount),
        ("Assigned To", col_assigned_to),
    ]
    
    for header_text, col in headers:
        cell = ws.cell(row=header_row, column=col)
        cell.value = header_text
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    
    # Collect all row indices that need highlighting (manual review)
    highlight_rows: set[int] = set()
    
    # Process each group
    for group in all_groups:
        # Get exchange rate for this group's date
        date_key = group.date.strftime("%Y-%m-%d") if group.date else ""
        ex_rate = exchange_rates.get(date_key) if exchange_rates else None
        
        # Get month from date
        month_str = group.date.strftime("%Y%m") if group.date else ""
        
        # Bank label
        bank_label = group.bank_type.value  # 29 or 30
        
        # Mark manual review rows
        if group.requires_manual_review:
            for row in group.rows:
                highlight_rows.add(row.original_row_index + 1)
        
        for i, row in enumerate(group.rows):
            excel_row = row.original_row_index + 1  # Excel is 1-based
            
            # Skip if row index is invalid
            if excel_row <= header_row:
                continue
            
            # Exchange rate (only for USD bank and not first entry)
            if group.bank_type == BankType.USD and ex_rate:
                ws.cell(row=excel_row, column=col_exchange_rate).value = ex_rate
            
            # Bank label
            ws.cell(row=excel_row, column=col_bank_label).value = bank_label
            
            # Month
            ws.cell(row=excel_row, column=col_month).value = month_str
            
            # For non-bank entries, add account number and nature
            is_first_entry = (i == 0)
            if not is_first_entry and not row.is_bank_entry():
                # Account number
                account_num = row.get_account_number()
                if account_num:
                    ws.cell(row=excel_row, column=col_account_num).value = account_num
                
                # Nature category
                if row.nature_category:
                    nature_display = NATURE_DISPLAY_MAP.get(
                        row.nature_category, 
                        row.nature_category
                    )
                    ws.cell(row=excel_row, column=col_nature).value = nature_display
            
            # Converted amount (VND for VND bank, USD for USD bank)
            if row.amount is not None:
                if group.bank_type == BankType.USD and ex_rate:
                    # Convert VND amount to USD
                    converted = row.amount / ex_rate
                    ws.cell(row=excel_row, column=col_converted_amount).value = converted
                else:
                    # VND bank - amount is already in VND
                    ws.cell(row=excel_row, column=col_converted_amount).value = row.amount
            
            # Assigned To - show which section and category this is assigned to
            assigned_label = _get_assigned_to_label(group, row)
            if assigned_label:
                ws.cell(row=excel_row, column=col_assigned_to).value = assigned_label
    
    # Apply highlighting for manual review rows
    for row_idx in highlight_rows:
        for col in range(1, col_assigned_to + 1):
            cell = ws.cell(row=row_idx, column=col)
            cell.fill = HIGHLIGHT_FILL
    
    # Save to bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.read()


def _get_assigned_to_label(group, row) -> str:
    """
    Generate the 'Assigned To' label for a row based on the group's processing.
    
    Returns labels like:
    - "Income: Contribution"
    - "Income: Interest"
    - "Advance/Settlement: Advance"
    - "By nature: Organisational capacity building"
    """
    # Check group's processed section first
    section = getattr(group, 'processed_section', None)
    
    if section == "income":
        # Determine which income type
        if group.has_deposit_type() and group.any_name_contains("onesky"):
            return "Income: Contribution"
        if group.any_memo_contains("transfer") and not group.any_memo_contains("elc"):
            return "Income: Fund transfer"
        if group.has_deposit_type() and group.any_memo_contains("interest"):
            return "Income: Interest"
        if group.has_deposit_type() and group.any_memo_contains("ped"):
            return "Income: PED"
        return "Income"
    
    elif section == "advance_settlement":
        if group.any_memo_contains("advance") and not group.any_memo_contains("settlement"):
            return "Advance/Settlement: Advance"
        if group.any_memo_contains("settlement"):
            return "Advance/Settlement: Settlement"
        return "Advance/Settlement"
    
    elif section == "nature":
        # Use the row's nature category
        if row.nature_category:
            nature_display = NATURE_DISPLAY_MAP.get(row.nature_category, row.nature_category)
            return f"By nature: {nature_display}"
        return "By nature"
    
    elif section == "manual":
        if row.nature_category:
            nature_display = NATURE_DISPLAY_MAP.get(row.nature_category, row.nature_category)
            return f"Manual input: {nature_display}"
        return "Manual input"
    
    # If no section assigned but row has nature
    if row.nature_category:
        nature_display = NATURE_DISPLAY_MAP.get(row.nature_category, row.nature_category)
        return f"By nature: {nature_display}"
    
    return ""
