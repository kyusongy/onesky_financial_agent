"""
Report generator for filling the output template.
"""
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from pathlib import Path
from typing import Optional, BinaryIO, Union
from io import BytesIO
from copy import copy

from .models import TransactionGroup, ProcessingResult, BankType
from config.mappings import (
    DEFAULT_OUTPUT_TEMPLATE,
    TEMPLATE_COL_VND,
    TEMPLATE_COL_USD,
    TEMPLATE_ROWS,
)


# Yellow highlight for manual review
HIGHLIGHT_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")


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
        """Fill income section values."""
        for bank_type, values in result.income.items():
            col = self._get_column(bank_type)
            
            # Contribution (row 5 in Excel = index 4)
            self._set_cell(ws, TEMPLATE_ROWS["contribution"], col, values.get("contribution", 0))
            
            # Fund transfer (row 6 in Excel = index 5)
            self._set_cell(ws, TEMPLATE_ROWS["fund_transfer"], col, values.get("fund_transfer", 0))
            
            # Interest (row 7 in Excel = index 6)
            self._set_cell(ws, TEMPLATE_ROWS["interest"], col, values.get("interest", 0))
            
            # PED (row 8 in Excel = index 7)
            self._set_cell(ws, TEMPLATE_ROWS["ped"], col, values.get("ped", 0))
    
    def _fill_advance_settlement(self, ws, result: ProcessingResult) -> None:
        """Fill advance/settlement section values."""
        for bank_type, values in result.advance_settlement.items():
            col = self._get_column(bank_type)
            
            # Advance (row 37 in Excel = index 36)
            self._set_cell(ws, TEMPLATE_ROWS["advance"], col, values.get("advance", 0))
            
            # Settlement (row 38 in Excel = index 37)
            self._set_cell(ws, TEMPLATE_ROWS["settlement"], col, values.get("settlement", 0))
    
    def _fill_nature(self, ws, result: ProcessingResult) -> None:
        """Fill expenditure by nature section values."""
        for bank_type, values in result.nature_totals.items():
            col = self._get_column(bank_type)
            
            # Org (row 13 in Excel = index 12)
            self._set_cell(ws, TEMPLATE_ROWS["org"], col, values.get("org", 0))
            
            # Edu (row 14 in Excel = index 13)
            self._set_cell(ws, TEMPLATE_ROWS["edu"], col, values.get("edu", 0))
            
            # Oper (row 15 in Excel = index 14)
            self._set_cell(ws, TEMPLATE_ROWS["oper"], col, values.get("oper", 0))
            
            # Nutrition (row 16 in Excel = index 15)
            self._set_cell(ws, TEMPLATE_ROWS["nutrition"], col, values.get("nutrition", 0))
            
            # Edu Infra (row 17 in Excel = index 16)
            self._set_cell(ws, TEMPLATE_ROWS["edu_infra"], col, values.get("edu_infra", 0))
            
            # Manual (row 41 in Excel = index 40)
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
    manual_groups: list[TransactionGroup]
) -> bytes:
    """
    Generate a copy of the transaction file with manual review rows highlighted.
    
    Args:
        original_file: Path or file-like object of original transaction file
        manual_groups: List of groups requiring manual review
        
    Returns:
        Bytes of the highlighted Excel file
    """
    wb = load_workbook(original_file)
    ws = wb.active
    
    # Collect all row indices that need highlighting
    highlight_rows: set[int] = set()
    for group in manual_groups:
        for row in group.rows:
            # Excel rows are 1-based
            highlight_rows.add(row.original_row_index + 1)
    
    # Apply highlighting
    for row_idx in highlight_rows:
        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_idx, column=col)
            cell.fill = HIGHLIGHT_FILL
    
    # Save to bytes
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.read()

