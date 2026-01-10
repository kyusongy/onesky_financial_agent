"""
Validation data tracking for report verification.

This module provides a ValidationData class that tracks per-row contributions
to each report column. When summed by bank, these should match the report values.
"""
from typing import Optional


# The 12 validation column internal keys
VALIDATION_COLUMNS = [
    "contribution",
    "fund_transfer",
    "interest",
    "cash_settlement",
    "fund_transfer_elc",
    "org",
    "edu",
    "oper",
    "nutrition",
    "edu_infra",
    "advance",
    "settlement",
]

# Display names for Excel headers
VALIDATION_COLUMN_DISPLAY = {
    "contribution": "Contribution",
    "fund_transfer": "Fund transfer to USD account",
    "interest": "Interest",
    "cash_settlement": "Cash settlement",
    "fund_transfer_elc": "Fund transfer to ELC",
    "org": "Organisational capacity building",
    "edu": "Education quality improvement",
    "oper": "Program Operation",
    "nutrition": "Nutrion for the children",
    "edu_infra": "Education Infrastructure",
    "advance": "Advance by cash",
    "settlement": "Settlement",
}


class ValidationData:
    """
    Tracks per-row contributions to report columns for validation.

    Maps original_row_index -> {column_name: amount}
    """

    def __init__(self):
        self.row_values: dict[int, dict[str, float]] = {}

    def set_value(self, row_index: int, column: str, amount: float) -> None:
        """
        Set the validation value for a specific row and column.

        Args:
            row_index: The original row index from the Excel file
            column: The column key (e.g., "contribution", "org", "advance")
            amount: The amount to record (in original currency)
        """
        if row_index not in self.row_values:
            self.row_values[row_index] = {}
        self.row_values[row_index][column] = amount

    def get_value(self, row_index: int, column: str) -> Optional[float]:
        """
        Get the validation value for a specific row and column.

        Args:
            row_index: The original row index from the Excel file
            column: The column key (e.g., "contribution", "org", "advance")

        Returns:
            The amount if set, None otherwise
        """
        return self.row_values.get(row_index, {}).get(column)

    def get_row_values(self, row_index: int) -> dict[str, float]:
        """
        Get all validation values for a specific row.

        Args:
            row_index: The original row index from the Excel file

        Returns:
            Dictionary of column -> amount for that row
        """
        return self.row_values.get(row_index, {})

    def get_column_total(self, column: str) -> float:
        """
        Get the total for a specific column across all rows.

        Args:
            column: The column key

        Returns:
            Sum of all values in that column
        """
        total = 0.0
        for row_data in self.row_values.values():
            if column in row_data:
                total += row_data[column]
        return total
