"""
OneSky Financial Report Automation Tool
Streamlit-based UI for processing transaction files and generating reports.
"""

import logging
import pandas as pd
import streamlit as st
from io import BytesIO
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

from src.parser import parse_transaction_file
from src.processor import process_transactions
from src.calculators.income import calculate_income
from src.calculators.advance_settlement import process_special_accounts
from src.calculators.nature import NatureMapper
from src.calculators.manual_input import ManualInputProcessor, mark_processed_groups
from src.calculators.province import ProvinceMapper
from src.report_generator import ReportGenerator, generate_marked_transactions
from src.models import ProcessingResult, TransactionGroup, BANK_USD, BANK_VND, BANK_34
from src.validation import ValidationData
from src.calculators.utils import extract_exchange_rate_from_memo
from src.lookup_parsers import (
    LookupParseError,
    parse_nature_upload,
    parse_staff_upload,
    parse_province_upload,
)
from config.mappings import PROVINCE_CODES

# Page configuration
st.set_page_config(
    page_title="OneSky Financial Report",
    page_icon="📊",
    layout="wide",
)

# Custom styling
st.markdown(
    """
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 600;
        color: #1e3a5f;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #6b7280;
        margin-bottom: 2rem;
    }
    .section-header {
        font-size: 1.3rem;
        font-weight: 500;
        color: #374151;
        margin-top: 1.5rem;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid #e5e7eb;
    }
    .success-box {
        padding: 1rem;
        background-color: #ecfdf5;
        border-radius: 0.5rem;
        border-left: 4px solid #10b981;
        margin: 1rem 0;
    }
    .warning-box {
        padding: 1rem;
        background-color: #fffbeb;
        border-radius: 0.5rem;
        border-left: 4px solid #f59e0b;
        margin: 1rem 0;
    }
    .stDownloadButton button {
        width: 100%;
    }
    .exchange-rate-input {
        background-color: #f8fafc;
        padding: 0.5rem;
        border-radius: 0.25rem;
    }
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Lookup override helpers (preview → confirm flow)
# ---------------------------------------------------------------------------


def _nature_to_df(parsed: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(sorted(parsed.items()), columns=["account", "nature"])


def _df_to_nature(df: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        k = str(row.get("account", "")).strip().lower()
        v = str(row.get("nature", "")).strip().lower()
        if k and v and v != "nan":
            out[k] = v
    return out


def _staff_to_df(
    parsed: tuple[dict[str, str], dict[str, dict[str, float]]],
) -> pd.DataFrame:
    staff_table, allocation_table = parsed
    provinces: list[str] = sorted(
        {p for alloc in allocation_table.values() for p in alloc.keys()}
    )
    names = sorted(set(staff_table.keys()) | set(allocation_table.keys()))
    rows = []
    for name in names:
        row = {"staff_name": name, "nature": staff_table.get(name, "")}
        alloc = allocation_table.get(name, {})
        for p in provinces:
            row[p] = float(alloc.get(p, 0.0))
        rows.append(row)
    return pd.DataFrame(rows)


def _df_to_staff(
    df: pd.DataFrame,
) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    staff_table: dict[str, str] = {}
    allocation_table: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        name = str(row.get("staff_name", "")).strip().lower()
        if not name or name == "nan":
            continue
        nature_val = row.get("nature", "")
        if pd.notna(nature_val) and str(nature_val).strip():
            staff_table[name] = str(nature_val).strip().lower()
        alloc: dict[str, float] = {}
        for col in df.columns:
            if col in ("staff_name", "nature"):
                continue
            val = row.get(col)
            if pd.notna(val):
                try:
                    num = float(val)
                    if num != 0:
                        alloc[str(col).lower()] = num
                except (ValueError, TypeError):
                    continue
        if alloc:
            allocation_table[name] = alloc
    return staff_table, allocation_table


def _province_to_df(parsed: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(parsed, columns=["pattern", "province_code"])


def _df_to_province(df: pd.DataFrame) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for _, row in df.iterrows():
        p = str(row.get("pattern", "")).strip().upper()
        c = str(row.get("province_code", "")).strip().lower()
        if p and c and p != "NAN" and c != "nan":
            out.append((p, c))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


LOOKUP_CONFIG = {
    "nature": {
        "label": "Nature Lookup",
        "parser": parse_nature_upload,
        "to_df": _nature_to_df,
        "from_df": _df_to_nature,
    },
    "staff": {
        "label": "Staff & Allocation Lookup",
        "parser": parse_staff_upload,
        "to_df": _staff_to_df,
        "from_df": _df_to_staff,
    },
    "province": {
        "label": "Province Pattern Lookup",
        "parser": parse_province_upload,
        "to_df": _province_to_df,
        "from_df": _df_to_province,
    },
}


def _handle_upload(kind: str, uploaded_file) -> None:
    """Parse a freshly-uploaded file, stash preview/error in session state."""
    cfg = LOOKUP_CONFIG[kind]
    sig_key = f"{kind}_sig"
    if uploaded_file is None:
        for suffix in ("sig", "preview_df", "override", "parse_error"):
            st.session_state.pop(f"{kind}_{suffix}", None)
        return

    sig = (uploaded_file.name, uploaded_file.size)
    if st.session_state.get(sig_key) == sig:
        return  # same file, already parsed

    # New or changed file — clear any previous override & error, reparse
    st.session_state[sig_key] = sig
    st.session_state.pop(f"{kind}_override", None)
    st.session_state.pop(f"{kind}_parse_error", None)
    try:
        parsed = cfg["parser"](BytesIO(uploaded_file.getvalue()))
        st.session_state[f"{kind}_preview_df"] = cfg["to_df"](parsed)
    except LookupParseError as e:
        st.session_state[f"{kind}_parse_error"] = e
        st.session_state.pop(f"{kind}_preview_df", None)


def _render_preview(kind: str) -> None:
    """Render parse error or preview editor + confirm button for one lookup."""
    cfg = LOOKUP_CONFIG[kind]
    err: LookupParseError | None = st.session_state.get(f"{kind}_parse_error")
    if err:
        loc = []
        if err.sheet:
            loc.append(f"sheet '{err.sheet}'")
        if err.row is not None:
            loc.append(f"row {err.row + 1}")
        detail = f" ({', '.join(loc)})" if loc else ""
        st.error(f"❌ {cfg['label']} parse failed{detail}: {err.message}")
        return

    preview_df = st.session_state.get(f"{kind}_preview_df")
    if preview_df is None:
        return

    override_active = st.session_state.get(f"{kind}_override") is not None
    with st.expander(
        f"🔍 Preview & Confirm — {cfg['label']}" + (" ✓" if override_active else ""),
        expanded=not override_active,
    ):
        edited = st.data_editor(
            preview_df,
            num_rows="dynamic",
            key=f"{kind}_editor",
            use_container_width=True,
        )

        # Province-only: warn on unknown codes (non-blocking)
        if kind == "province":
            unknown = {
                str(c).strip().lower()
                for c in edited.get("province_code", [])
                if pd.notna(c) and str(c).strip().lower() not in PROVINCE_CODES
            }
            if unknown:
                st.warning(
                    f"⚠ Unknown province codes: {sorted(unknown)} — transactions "
                    "matching these patterns won't appear in any report row."
                )

        if st.button(f"Confirm {cfg['label']}", key=f"{kind}_confirm"):
            st.session_state[f"{kind}_override"] = cfg["from_df"](edited)
            st.rerun()

        if override_active:
            st.success(f"✓ {cfg['label']} override active")


def _overrides_pending() -> bool:
    """True if any upload has a preview/error but no confirmed override."""
    for kind in LOOKUP_CONFIG:
        if st.session_state.get(f"{kind}_parse_error") is not None:
            return True
        if (
            st.session_state.get(f"{kind}_preview_df") is not None
            and st.session_state.get(f"{kind}_override") is None
        ):
            return True
    return False


# ---------------------------------------------------------------------------


def _display_bank_tab(section_data: dict, format_key):
    """Display a three-column bank comparison tab (VND left, USD middle, 34 right)."""
    col1, col2, col3 = st.columns(3)
    for col, bank_id, label in [
        (col1, BANK_VND, "VND Bank (30)"),
        (col2, BANK_USD, "USD Bank (29)"),
        (col3, BANK_34, "VND Bank (34)"),
    ]:
        with col:
            st.markdown(f"**{label}**")
            total = 0
            bank_data = section_data.get(bank_id, {})
            for key, val in bank_data.items():
                if val != 0:
                    st.write(f"- {format_key(key)}: {val:,.0f}")
                    total += val
            st.markdown(f"**Total: {total:,.0f}**")


def get_usd_groups(groups) -> list[TransactionGroup]:
    """Get USD transaction groups for per-transaction exchange rate input."""
    return [g for g in groups if g.bank_identifier == BANK_USD]


def main():
    # Header
    st.markdown(
        '<p class="main-header">📊 OneSky Financial Report Automation</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="sub-header">Upload transaction files, process data, and generate reports</p>',
        unsafe_allow_html=True,
    )

    # Initialize session state
    if "processing_complete" not in st.session_state:
        st.session_state.processing_complete = False
    if "result" not in st.session_state:
        st.session_state.result = None
    if "transaction_bytes" not in st.session_state:
        st.session_state.transaction_bytes = None
    if "parsed_groups" not in st.session_state:
        st.session_state.parsed_groups = None
    if "usd_groups" not in st.session_state:
        st.session_state.usd_groups = []
    if "exchange_rates" not in st.session_state:
        st.session_state.exchange_rates = {}
    if "nature_mapper" not in st.session_state:
        st.session_state.nature_mapper = None

    # Sidebar for file uploads
    with st.sidebar:
        st.markdown("### 📁 File Upload")

        # Transaction file upload
        transaction_file = st.file_uploader(
            "Transaction File (.xlsx)",
            type=["xlsx"],
            help="Upload the monthly transaction file (e.g., Transaction.xlsx)",
            key="transaction_upload",
        )

        st.markdown("---")

        # Optional: Custom lookup tables
        st.markdown("### ⚙️ Optional Lookup Overrides")
        st.caption(
            "Leave empty to use the packaged defaults. If uploaded, you must "
            "preview and confirm in the main panel before processing."
        )

        nature_lookup_file = st.file_uploader(
            "Nature lookup (nature.xlsx)",
            type=["xlsx"],
            help="Account → nature mapping. Sheet: Lookup1_AcctList.",
            key="nature_upload",
        )
        _handle_upload("nature", nature_lookup_file)

        staff_allocation_file = st.file_uploader(
            "Staff & allocation lookup (staff.xlsx)",
            type=["xlsx"],
            help="Staff name → nature + province allocation. Sheet: Lookup3_Staff & allocation.",
            key="staff_allocation_upload",
        )
        _handle_upload("staff", staff_allocation_file)

        province_patterns_file = st.file_uploader(
            "Province pattern lookup (province_patterns.xlsx)",
            type=["xlsx"],
            help="Memo pattern → province code. Columns: 'pattern', 'province_code'.",
            key="province_patterns_upload",
        )
        _handle_upload("province", province_patterns_file)

    # Lookup override preview / confirm (renders regardless of transaction file,
    # so users can vet overrides before uploading transactions).
    any_upload = any(
        st.session_state.get(f"{k}_preview_df") is not None
        or st.session_state.get(f"{k}_parse_error") is not None
        for k in LOOKUP_CONFIG
    )
    if any_upload:
        st.markdown(
            '<p class="section-header">🧾 Confirm Lookup Overrides</p>',
            unsafe_allow_html=True,
        )
        for kind in LOOKUP_CONFIG:
            _render_preview(kind)

    # Main content area
    if transaction_file is None:
        st.info("👈 Please upload a transaction file to begin processing")

        # Show instructions
        with st.expander("📖 How to use this tool"):
            st.markdown("""
            1. **Upload Transaction File**: Select your monthly transaction Excel file
            2. **Enter Exchange Rates**: If USD transactions exist, enter the exchange rate for each date
            3. **Process**: Click the process button to analyze transactions
            4. **Download Results**: Download the filled report and marked transaction file
            
            **Supported Features:**
            - Income calculation (Contribution, Fund Transfer USD, Fund Transfer ELC, Interest, PED)
            - Advance/Settlement tracking
            - Expenditure by nature categorization
            - Manual review highlighting for complex transactions
            - Per-date exchange rate support for USD transactions
            """)
        return

    # Store transaction bytes for later use
    st.session_state.transaction_bytes = transaction_file.read()
    transaction_file.seek(0)  # Reset for processing

    # Parse transaction file to check for USD transactions
    with st.spinner("Analyzing transaction file..."):
        try:
            groups, has_usd = parse_transaction_file(
                BytesIO(st.session_state.transaction_bytes)
            )
            st.session_state.parsed_groups = groups
            st.success(f"✓ Loaded {len(groups)} transaction groups")

            # Get USD groups for per-transaction exchange rate input
            if has_usd:
                st.session_state.usd_groups = get_usd_groups(groups)
        except Exception as e:
            st.error(f"Error parsing transaction file: {str(e)}")
            return

    # Exchange rate input section for each USD transaction
    if st.session_state.usd_groups:
        st.markdown(
            '<p class="section-header">Exchange Rates</p>', unsafe_allow_html=True
        )
        st.markdown(
            '<div class="warning-box">USD transactions detected. Please verify the exchange rate for each transaction below.</div>',
            unsafe_allow_html=True,
        )

        num_groups = len(st.session_state.usd_groups)
        cols_per_row = 3

        for i in range(0, num_groups, cols_per_row):
            cols = st.columns(cols_per_row)
            for j, col in enumerate(cols):
                idx = i + j
                if idx < num_groups:
                    group = st.session_state.usd_groups[idx]
                    group_id = group.group_id
                    date_str = group.date.strftime("%Y-%m-%d") if group.date else "N/A"
                    payee = group.payee_name or "Unknown"
                    amount = group.bank_amount

                    # Auto-extract rate from memo, fall back to 25000
                    memo_rate = extract_exchange_rate_from_memo(group.entries)
                    default_rate = memo_rate or 25000.0

                    with col:
                        label = f"{date_str} | {payee[:20]} | {amount:,.0f}"
                        rate = st.number_input(
                            label,
                            min_value=1.0,
                            max_value=100000.0,
                            value=st.session_state.exchange_rates.get(
                                group_id, default_rate
                            ),
                            step=100.0,
                            key=f"rate_{group_id}",
                            help=f"VND/USD rate. {'Auto-detected from memo.' if memo_rate else 'Default 25,000.'}",
                        )
                        st.session_state.exchange_rates[group_id] = rate

    # Process button
    st.markdown('<p class="section-header">🔄 Processing</p>', unsafe_allow_html=True)

    overrides_pending = _overrides_pending()
    if overrides_pending:
        st.warning(
            "Resolve uploaded lookup overrides above (fix errors or click "
            "Confirm) before processing."
        )

    if st.button(
        "🚀 Process Transactions",
        type="primary",
        use_container_width=True,
        disabled=overrides_pending,
    ):
        with st.spinner("Processing transactions..."):
            try:
                # Step 1: Use already parsed groups
                groups = st.session_state.parsed_groups

                # Step 2: Assign exchange rates to groups (per-transaction, keyed by group_id)
                for group in groups:
                    if group.bank_identifier == BANK_USD:
                        group.exchange_rate = st.session_state.exchange_rates.get(
                            group.group_id, 1.0
                        )

                # Step 3: Process groups (split transfers, remove capital rows)
                groups_by_bank = process_transactions(groups)

                # Create ValidationData to track per-row contributions
                validation_data = ValidationData()

                # Step 4: Calculate income (with exchange rates for USD conversion)
                income = calculate_income(
                    groups_by_bank, st.session_state.exchange_rates, validation_data
                )

                # Step 5: Process special accounts (1230/1250/1252 -> adv/settle, 1500 -> payable)
                advance_settlement, payable_totals = process_special_accounts(
                    groups_by_bank, st.session_state.exchange_rates, validation_data
                )

                # Step 6: Map nature categories (with exchange rates for USD conversion)
                nature_override = st.session_state.get("nature_override")
                staff_override = st.session_state.get("staff_override")
                province_override = st.session_state.get("province_override")

                nature_mapper = NatureMapper(lookup=nature_override)
                st.session_state.nature_mapper = nature_mapper

                nature_totals, manual_groups = nature_mapper.process_groups(
                    groups_by_bank, st.session_state.exchange_rates, validation_data
                )

                logger.debug("nature_totals AFTER nature_mapper.process_groups")
                logger.debug(
                    "VND totals: %s",
                    {k: v for k, v in nature_totals[BANK_VND].items() if v != 0},
                )
                logger.debug(
                    "USD totals: %s",
                    {k: v for k, v in nature_totals[BANK_USD].items() if v != 0},
                )
                logger.debug("Manual groups count: %d", len(manual_groups))

                # Step 7: Mark processed groups (for avoiding double counting)
                mark_processed_groups(groups_by_bank)

                processed_count = sum(
                    1
                    for grps in groups_by_bank.values()
                    for g in grps
                    if g.is_processed
                )
                logger.debug(
                    "After mark_processed_groups: Total processed groups: %d",
                    processed_count,
                )

                # Step 8: Process manual input groups
                manual_processor = ManualInputProcessor(staff_data=staff_override)

                manual_nature_totals, still_manual = (
                    manual_processor.process_manual_groups(
                        manual_groups, st.session_state.exchange_rates, validation_data
                    )
                )

                logger.debug("BEFORE merging manual_nature_totals")
                logger.debug(
                    "nature_totals[VND]: %s",
                    {k: v for k, v in nature_totals[BANK_VND].items() if v != 0},
                )
                logger.debug(
                    "manual_nature_totals[VND]: %s",
                    {k: v for k, v in manual_nature_totals[BANK_VND].items() if v != 0},
                )

                # Merge manual nature totals into nature totals
                for bank_id in [BANK_USD, BANK_VND, BANK_34]:
                    for key, value in manual_nature_totals[bank_id].items():
                        if key in nature_totals[bank_id]:
                            nature_totals[bank_id][key] += value

                # Clear the "manual" tracking category - those amounts are now categorized
                # Only keep "manual" amount for groups that are still unprocessed (still_manual)
                for bank_id in [BANK_USD, BANK_VND, BANK_34]:
                    if "manual" in nature_totals[bank_id]:
                        # Calculate remaining manual amount from still_manual groups
                        remaining_manual = sum(
                            abs(g.bank_amount)
                            for g in still_manual
                            if g.bank_identifier == bank_id
                        )
                        nature_totals[bank_id]["manual"] = remaining_manual

                logger.debug("AFTER merging - FINAL totals")
                logger.debug(
                    "nature_totals[VND]: %s",
                    {k: v for k, v in nature_totals[BANK_VND].items() if v != 0},
                )
                logger.debug(
                    "advance_settlement[VND]: %s",
                    {k: v for k, v in advance_settlement[BANK_VND].items() if v != 0},
                )
                logger.debug(
                    "payable_totals[VND]: %s",
                    {k: v for k, v in payable_totals[BANK_VND].items() if v != 0},
                )

                # Step 9: Calculate province totals
                province_mapper = ProvinceMapper(
                    allocation_data=staff_override[1] if staff_override else None,
                    patterns=province_override,
                )
                province_totals = province_mapper.process_groups(
                    groups_by_bank, st.session_state.exchange_rates, validation_data
                )

                logger.debug("FINAL province_totals")
                logger.debug(
                    "VND: %s",
                    {k: v for k, v in province_totals[BANK_VND].items() if v != 0},
                )
                logger.debug(
                    "USD: %s",
                    {k: v for k, v in province_totals[BANK_USD].items() if v != 0},
                )

                # Collect all groups for marked transaction output
                all_groups = []
                for bank_groups in groups_by_bank.values():
                    all_groups.extend(bank_groups)

                # Create processing result
                result = ProcessingResult(
                    groups_by_bank=groups_by_bank,
                    income=income,
                    advance_settlement=advance_settlement,
                    payable_totals=payable_totals,
                    nature_totals=nature_totals,
                    province_totals=province_totals,
                    manual_groups=still_manual,  # Only groups that still need manual review
                    all_groups=all_groups,
                    exchange_rates=st.session_state.exchange_rates,
                    validation_data=validation_data,
                )

                st.session_state.result = result
                st.session_state.processing_complete = True

                st.markdown(
                    '<div class="success-box">✓ Processing complete!</div>',
                    unsafe_allow_html=True,
                )

            except Exception as e:
                st.error(f"Error during processing: {str(e)}")
                import traceback

                st.code(traceback.format_exc())
                return

    # Display results and download buttons
    if st.session_state.processing_complete and st.session_state.result:
        result = st.session_state.result

        # Summary statistics
        st.markdown('<p class="section-header">📈 Summary</p>', unsafe_allow_html=True)

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            total_groups = sum(len(g) for g in result.groups_by_bank.values())
            st.metric("Total Groups", total_groups)

        with col2:
            st.metric("Manual Review", len(result.manual_groups))

        with col3:
            st.metric("USD Transactions", len(st.session_state.usd_groups))

        with col4:
            vnd_income = result.get_income_total(BANK_VND)
            st.metric("VND Income", f"{vnd_income:,.0f}")

        # Detailed breakdown
        with st.expander("📊 Detailed Breakdown", expanded=True):
            tab1, tab2, tab3, tab4, tab5 = st.tabs(
                ["Income", "Advance/Settlement", "Payable", "By Nature", "By Province"]
            )

            with tab1:
                _display_bank_tab(result.income, lambda k: k.replace("_", " ").title())
            with tab2:
                _display_bank_tab(result.advance_settlement, lambda k: k.title())
            with tab3:
                _display_bank_tab(result.payable_totals, lambda k: k.title())
            with tab4:
                _display_bank_tab(result.nature_totals, lambda k: k.upper())
            with tab5:
                _display_bank_tab(result.province_totals, lambda k: k.upper())

        # Download section
        st.markdown(
            '<p class="section-header">📥 Download Results</p>', unsafe_allow_html=True
        )

        col1, col2 = st.columns(2)

        with col1:
            # Generate filled report
            try:
                report_generator = ReportGenerator()
                report_bytes = report_generator.generate_report(result)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    label="📄 Download Filled Report",
                    data=report_bytes,
                    file_name=f"filled_report_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Error generating report: {str(e)}")

        with col2:
            # Generate marked transaction file
            try:
                marked_bytes = generate_marked_transactions(
                    BytesIO(st.session_state.transaction_bytes),
                    result.all_groups,
                    result.exchange_rates,
                    st.session_state.nature_mapper,
                    result.validation_data,
                )

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                st.download_button(
                    label="📋 Download Processed Transactions",
                    data=marked_bytes,
                    file_name=f"processed_transactions_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Error generating marked file: {str(e)}")
                import traceback

                st.code(traceback.format_exc())


if __name__ == "__main__":
    main()
