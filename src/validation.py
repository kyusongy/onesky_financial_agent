"""
Validation data tracking for report verification.

This module provides a ValidationData class that tracks per-row contributions
to each report column. When summed by bank, these should match the report values.
"""
from typing import Optional


# The 13 validation column internal keys for Nature section (including payable)
NATURE_VALIDATION_COLUMNS = [
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
    "payable",  # NEW: For 1500 accounts
]

# The 16 province validation column internal keys
PROVINCE_VALIDATION_COLUMNS = [
    "elc",
    "vnelc",
    "vnhbc",
    "vndn",
    "vnqn",
    "vnhd",
    "vnqng",
    "vnmoet",
    "vnbd",
    "vnbg",
    "vnla",
    "vnhcm",
    "vnbn",
    "vnother",
    "caobang",
    "province_manual",
]

# Combined validation columns (nature + province)
VALIDATION_COLUMNS = NATURE_VALIDATION_COLUMNS + PROVINCE_VALIDATION_COLUMNS

# Display names for Excel headers
VALIDATION_COLUMN_DISPLAY = {
    # Nature section columns
    "contribution": "Contribution",
    "fund_transfer": "Fund transfer to USD account",
    "interest": "Interest",
    "cash_settlement": "Cash settlement",
    "fund_transfer_elc": "Fund transfer to ELC",
    "org": "Organisational capacity building",
    "edu": "Education quality improvement",
    "oper": "Program Operation",
    "nutrition": "Nutrition for the children",
    "edu_infra": "Education Infrastructure",
    "advance": "Advance by cash",
    "settlement": "Settlement",
    "payable": "Payable",
    # Province section columns
    "elc": "ELC Operation",
    "vnelc": "ELC training",
    "vnhbc": "VN General National Training",
    "vndn": "Da Nang ICC",
    "vnqn": "Quang Nam ICC",
    "vnhd": "Hai Duong ICC",
    "vnqng": "Quang Ngai ICC",
    "vnmoet": "Preparation and general of MOET",
    "vnbd": "Binh Duong MOET",
    "vnbg": "Bac Giang MOET",
    "vnla": "Long An MOET",
    "vnhcm": "HCM MOET",
    "vnbn": "Bac Ninh MOET",
    "vnother": "In-country program support",
    "caobang": "Cao Bang",
    "province_manual": "Province Manual",
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
