"""
Strict parsers for user-uploaded lookup files.

Unlike the loaders in `src/calculators/`, these raise LookupParseError on any
malformed input instead of silently returning empty dicts. Used by the
Streamlit UI to surface upload errors to the user.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Union

import pandas as pd

from .calculators.utils import normalize_nature

logger = logging.getLogger(__name__)

Source = Union[str, Path, BytesIO]


class LookupParseError(Exception):
    """Raised when an uploaded lookup file cannot be parsed."""

    def __init__(self, message: str, sheet: str | None = None, row: int | None = None):
        self.message = message
        self.sheet = sheet
        self.row = row
        super().__init__(message)


def _read_sheet(src: Source, sheet_name: str) -> pd.DataFrame:
    try:
        return pd.read_excel(src, sheet_name=sheet_name, header=None, engine="openpyxl")
    except ValueError as e:
        raise LookupParseError(
            f"Sheet '{sheet_name}' not found in uploaded file", sheet=sheet_name
        ) from e
    except Exception as e:
        raise LookupParseError(f"Could not open file: {e}") from e


def _find_header_row(df: pd.DataFrame, needle: str, max_scan: int = 10) -> int:
    for idx in range(min(max_scan, len(df))):
        for val in df.iloc[idx].values:
            if isinstance(val, str) and needle.lower() in val.lower():
                return idx
    raise LookupParseError(f"Header row containing '{needle}' not found")


def parse_nature_upload(src: Source) -> dict[str, str]:
    """Parse user-uploaded nature.xlsx into canonical {account_lower: nature_lower}.

    Expected sheet: "Lookup1_AcctList". Column A = Account No., Column B = nature.
    """
    df = _read_sheet(src, "Lookup1_AcctList")
    try:
        header_idx = _find_header_row(df, "account no")
    except LookupParseError as e:
        raise LookupParseError(e.message, sheet="Lookup1_AcctList") from e

    lookup: dict[str, str] = {}
    for idx in range(header_idx + 1, len(df)):
        row = df.iloc[idx]
        account_no = row.iloc[0] if len(row) > 0 else None
        nature = row.iloc[1] if len(row) > 1 else None
        if pd.notna(account_no) and pd.notna(nature):
            lookup[str(account_no).strip().lower()] = str(nature).strip().lower()

    if not lookup:
        raise LookupParseError(
            "No account mappings found below header",
            sheet="Lookup1_AcctList",
            row=header_idx + 1,
        )
    return lookup


def parse_staff_upload(
    src: Source,
) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    """Parse user-uploaded staff.xlsx into (staff_lookup, allocation_lookup).

    Expected sheet: "Lookup3_Staff & allocation". Column A = name, B = nature,
    C onward = province allocations (column headers become province keys).
    """
    sheet_name = "Lookup3_Staff & allocation"
    df = _read_sheet(src, sheet_name)
    try:
        header_idx = _find_header_row(df, "staff name")
    except LookupParseError as e:
        raise LookupParseError(e.message, sheet=sheet_name) from e

    header_row = df.iloc[header_idx]
    province_cols: dict[int, str] = {}
    for col_idx in range(2, len(header_row)):
        col_name = header_row.iloc[col_idx]
        if pd.notna(col_name) and str(col_name).strip():
            province_cols[col_idx] = str(col_name).strip().lower()

    staff_table: dict[str, str] = {}
    allocation_table: dict[str, dict[str, float]] = {}

    for idx in range(header_idx + 1, len(df)):
        row = df.iloc[idx]
        name = row.iloc[0] if len(row) > 0 else None
        if pd.isna(name) or not str(name).strip():
            continue
        name_key = str(name).strip().lower()

        nature_val = row.iloc[1] if len(row) > 1 else None
        if pd.notna(nature_val) and str(nature_val).strip():
            staff_table[name_key] = normalize_nature(str(nature_val).strip())

        allocations: dict[str, float] = {}
        for col_idx, province in province_cols.items():
            val = row.iloc[col_idx] if len(row) > col_idx else None
            if pd.notna(val) and val not in (0, 0.0, "", None):
                try:
                    allocations[province] = float(val)
                except (ValueError, TypeError):
                    pass
        if allocations:
            allocation_table[name_key] = allocations

    if not staff_table and not allocation_table:
        raise LookupParseError(
            "No staff or allocation rows found below header",
            sheet=sheet_name,
            row=header_idx + 1,
        )
    return staff_table, allocation_table


def parse_province_upload(src: Source) -> list[tuple[str, str]]:
    """Parse user-uploaded province_patterns.xlsx into sorted [(pattern_upper, code_lower), ...].

    Expected: first sheet with columns "pattern" and "province_code". Output
    is sorted longest-pattern-first so `VNELC` beats `ELC` during matching.
    """
    try:
        df = pd.read_excel(src, engine="openpyxl")
    except Exception as e:
        raise LookupParseError(f"Could not open file: {e}") from e

    cols_lower = {c.lower().strip(): c for c in df.columns if isinstance(c, str)}
    if "pattern" not in cols_lower or "province_code" not in cols_lower:
        raise LookupParseError(
            "Expected columns 'pattern' and 'province_code' in first sheet"
        )

    pattern_col = cols_lower["pattern"]
    code_col = cols_lower["province_code"]

    result: list[tuple[str, str]] = []
    for idx, row in df.iterrows():
        p = row[pattern_col]
        c = row[code_col]
        if pd.isna(p) or pd.isna(c):
            continue
        p_str = str(p).strip().upper()
        c_str = str(c).strip().lower()
        if p_str and c_str:
            result.append((p_str, c_str))

    if not result:
        raise LookupParseError("No pattern/code rows found")

    result.sort(key=lambda x: len(x[0]), reverse=True)
    return result
