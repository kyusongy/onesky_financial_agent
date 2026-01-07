"""
OneSky Financial Report Automation Tool
Streamlit-based UI for processing transaction files and generating reports.
"""
import streamlit as st
from io import BytesIO
from datetime import datetime

from src.parser import parse_transaction_file
from src.processor import process_transactions
from src.calculators.income import calculate_income
from src.calculators.advance_settlement import calculate_advance_settlement
from src.calculators.nature import NatureMapper
from src.calculators.manual_input import ManualInputProcessor, mark_processed_groups
from src.report_generator import ReportGenerator, generate_marked_transactions
from src.models import ProcessingResult, BANK_USD, BANK_VND

# Page configuration
st.set_page_config(
    page_title="OneSky Financial Report",
    page_icon="📊",
    layout="wide",
)

# Custom styling
st.markdown("""
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
""", unsafe_allow_html=True)


def get_usd_dates(groups) -> list[str]:
    """Extract unique dates that have USD transactions."""
    usd_dates = set()
    for group in groups:
        if group.bank_identifier == BANK_USD and group.date:
            usd_dates.add(group.date.strftime("%Y-%m-%d"))
    return sorted(list(usd_dates))


def main():
    # Header
    st.markdown('<p class="main-header">📊 OneSky Financial Report Automation</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Upload transaction files, process data, and generate reports</p>', unsafe_allow_html=True)
    
    # Initialize session state
    if "processing_complete" not in st.session_state:
        st.session_state.processing_complete = False
    if "result" not in st.session_state:
        st.session_state.result = None
    if "transaction_bytes" not in st.session_state:
        st.session_state.transaction_bytes = None
    if "parsed_groups" not in st.session_state:
        st.session_state.parsed_groups = None
    if "usd_dates" not in st.session_state:
        st.session_state.usd_dates = []
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
            key="transaction_upload"
        )
        
        st.markdown("---")
        
        # Optional: Custom lookup tables
        st.markdown("### ⚙️ Optional Settings")
        nature_lookup_file = st.file_uploader(
            "Custom Nature Lookup Table",
            type=["xlsx"],
            help="Optional: Upload a custom nature lookup table to override the default",
            key="nature_upload"
        )
        
        if nature_lookup_file:
            st.success("✓ Custom nature lookup loaded")
        
        manual_lookup_file = st.file_uploader(
            "Custom Manual Input Lookup",
            type=["xlsx"],
            help="Optional: Upload a custom Manual.xlsx to override the default (contains 1250/1230/1252/1500 sheets)",
            key="manual_upload"
        )
        
        if manual_lookup_file:
            st.success("✓ Custom manual lookup loaded")
    
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
            groups, has_usd = parse_transaction_file(BytesIO(st.session_state.transaction_bytes))
            st.session_state.parsed_groups = groups
            st.success(f"✓ Loaded {len(groups)} transaction groups")
            
            # Get USD dates
            if has_usd:
                st.session_state.usd_dates = get_usd_dates(groups)
        except Exception as e:
            st.error(f"Error parsing transaction file: {str(e)}")
            return
    
    # Exchange rate input section for each USD date
    if st.session_state.usd_dates:
        st.markdown('<p class="section-header">💱 Exchange Rates</p>', unsafe_allow_html=True)
        st.markdown('<div class="warning-box">USD transactions detected. Please enter the exchange rate for each date below.</div>', unsafe_allow_html=True)
        
        # Create columns for exchange rate inputs
        num_dates = len(st.session_state.usd_dates)
        cols_per_row = 3
        
        for i in range(0, num_dates, cols_per_row):
            cols = st.columns(cols_per_row)
            for j, col in enumerate(cols):
                idx = i + j
                if idx < num_dates:
                    date_str = st.session_state.usd_dates[idx]
                    with col:
                        rate = st.number_input(
                            f"Rate for {date_str}",
                            min_value=1.0,
                            max_value=100000.0,
                            value=st.session_state.exchange_rates.get(date_str, 25000.0),
                            step=100.0,
                            key=f"rate_{date_str}",
                            help=f"VND to USD rate (e.g., 25000 = 1 USD = 25,000 VND)"
                        )
                        st.session_state.exchange_rates[date_str] = rate
    
    # Process button
    st.markdown('<p class="section-header">🔄 Processing</p>', unsafe_allow_html=True)
    
    if st.button("🚀 Process Transactions", type="primary", use_container_width=True):
        with st.spinner("Processing transactions..."):
            try:
                # Step 1: Use already parsed groups
                groups = st.session_state.parsed_groups
                
                # Step 2: Assign exchange rates to groups
                for group in groups:
                    if group.bank_identifier == BANK_USD and group.date:
                        date_key = group.date.strftime("%Y-%m-%d")
                        group.exchange_rate = st.session_state.exchange_rates.get(date_key, 1.0)
                
                # Step 3: Process groups (split transfers, remove capital rows)
                groups_by_bank = process_transactions(groups)
                
                # Step 4: Calculate income (with exchange rates for USD conversion)
                income = calculate_income(groups_by_bank, st.session_state.exchange_rates)
                
                # Step 5: Calculate advance/settlement (with exchange rates for USD conversion)
                advance_settlement = calculate_advance_settlement(groups_by_bank, st.session_state.exchange_rates)
                
                # Step 6: Map nature categories (with exchange rates for USD conversion)
                nature_mapper = NatureMapper(
                    BytesIO(nature_lookup_file.read()) if nature_lookup_file else None
                )
                if nature_lookup_file:
                    nature_lookup_file.seek(0)
                
                st.session_state.nature_mapper = nature_mapper
                
                nature_totals, manual_groups = nature_mapper.process_groups(groups_by_bank, st.session_state.exchange_rates)
                
                # DEBUG: Initial nature_totals from nature_mapper
                print("\n=== DEBUG: nature_totals AFTER nature_mapper.process_groups ===")
                print(f"VND totals: { {k: v for k, v in nature_totals[BANK_VND].items() if v != 0} }")
                print(f"USD totals: { {k: v for k, v in nature_totals[BANK_USD].items() if v != 0} }")
                print(f"Manual groups count: {len(manual_groups)}")
                
                # Step 7: Mark processed groups (for avoiding double counting)
                mark_processed_groups(groups_by_bank, income, advance_settlement, nature_totals)
                
                # DEBUG: Check how many groups are marked as processed after this
                processed_count = sum(1 for grps in groups_by_bank.values() for g in grps if g.is_processed)
                print(f"\n=== DEBUG: After mark_processed_groups ===")
                print(f"Total processed groups: {processed_count}")
                
                # Step 8: Process manual input groups
                manual_processor = ManualInputProcessor(
                    BytesIO(manual_lookup_file.read()) if manual_lookup_file else None
                )
                if manual_lookup_file:
                    manual_lookup_file.seek(0)
                
                manual_nature_totals, still_manual = manual_processor.process_manual_groups(
                    manual_groups, 
                    st.session_state.exchange_rates
                )
                
                # DEBUG: Before merging
                print("\n=== DEBUG: BEFORE merging manual_nature_totals ===")
                print(f"nature_totals[VND]: { {k: v for k, v in nature_totals[BANK_VND].items() if v != 0} }")
                print(f"manual_nature_totals[VND]: { {k: v for k, v in manual_nature_totals[BANK_VND].items() if v != 0} }")
                
                # Merge manual nature totals into main nature totals
                for bank_id in [BANK_USD, BANK_VND]:
                    for key, value in manual_nature_totals[bank_id].items():
                        if key in nature_totals[bank_id]:
                            old_val = nature_totals[bank_id][key]
                            nature_totals[bank_id][key] += value
                            if value != 0:
                                print(f"  MERGE {bank_id} {key}: {old_val} + {value} = {nature_totals[bank_id][key]}")
                        elif key in ["advance", "settlement"]:
                            # Add to advance/settlement section
                            advance_settlement[bank_id][key] += value

                # Clear the "manual" tracking category - those amounts are now categorized
                # Only keep "manual" amount for groups that are still unprocessed (still_manual)
                for bank_id in [BANK_USD, BANK_VND]:
                    if "manual" in nature_totals[bank_id]:
                        # Calculate remaining manual amount from still_manual groups
                        remaining_manual = sum(
                            abs(g.bank_amount) for g in still_manual
                            if g.bank_identifier == bank_id
                        )
                        nature_totals[bank_id]["manual"] = remaining_manual

                # DEBUG: After merging
                print("\n=== DEBUG: AFTER merging - FINAL nature_totals ===")
                print(f"VND: { {k: v for k, v in nature_totals[BANK_VND].items() if v != 0} }")
                
                # Collect all groups for marked transaction output
                all_groups = []
                for bank_groups in groups_by_bank.values():
                    all_groups.extend(bank_groups)
                
                # Create processing result
                result = ProcessingResult(
                    groups_by_bank=groups_by_bank,
                    income=income,
                    advance_settlement=advance_settlement,
                    nature_totals=nature_totals,
                    manual_groups=still_manual,  # Only groups that still need manual review
                    all_groups=all_groups,
                    exchange_rates=st.session_state.exchange_rates,
                )
                
                st.session_state.result = result
                st.session_state.processing_complete = True
                
                st.markdown('<div class="success-box">✓ Processing complete!</div>', unsafe_allow_html=True)
                
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
            st.metric("USD Dates", len(st.session_state.usd_dates))
        
        with col4:
            vnd_income = result.get_income_total(BANK_VND)
            st.metric("VND Income", f"{vnd_income:,.0f}")
        
        # Detailed breakdown
        with st.expander("📊 Detailed Breakdown", expanded=True):
            tab1, tab2, tab3 = st.tabs(["Income", "Advance/Settlement", "By Nature"])
            
            with tab1:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**VND Bank (30)**")
                    vnd_total = 0
                    for key, val in result.income[BANK_VND].items():
                        if val != 0:
                            st.write(f"- {key.replace('_', ' ').title()}: {val:,.0f}")
                            vnd_total += val
                    st.markdown(f"**Total: {vnd_total:,.0f}**")
                with col2:
                    st.markdown("**USD Bank (29)**")
                    usd_total = 0
                    for key, val in result.income[BANK_USD].items():
                        if val != 0:
                            st.write(f"- {key.replace('_', ' ').title()}: {val:,.0f}")
                            usd_total += val
                    st.markdown(f"**Total: {usd_total:,.0f}**")
            
            with tab2:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**VND Bank (30)**")
                    vnd_total = 0
                    for key, val in result.advance_settlement[BANK_VND].items():
                        if val != 0:
                            st.write(f"- {key.title()}: {val:,.0f}")
                            vnd_total += val
                    st.markdown(f"**Total: {vnd_total:,.0f}**")
                with col2:
                    st.markdown("**USD Bank (29)**")
                    usd_total = 0
                    for key, val in result.advance_settlement[BANK_USD].items():
                        if val != 0:
                            st.write(f"- {key.title()}: {val:,.0f}")
                            usd_total += val
                    st.markdown(f"**Total: {usd_total:,.0f}**")
            
            with tab3:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**VND Bank (30)**")
                    vnd_total = 0
                    for key, val in result.nature_totals[BANK_VND].items():
                        if val != 0:
                            st.write(f"- {key.upper()}: {val:,.0f}")
                            vnd_total += val
                    st.markdown(f"**Total: {vnd_total:,.0f}**")
                with col2:
                    st.markdown("**USD Bank (29)**")
                    usd_total = 0
                    for key, val in result.nature_totals[BANK_USD].items():
                        if val != 0:
                            st.write(f"- {key.upper()}: {val:,.0f}")
                            usd_total += val
                    st.markdown(f"**Total: {usd_total:,.0f}**")
        
        # Download section
        st.markdown('<p class="section-header">📥 Download Results</p>', unsafe_allow_html=True)
        
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
                    st.session_state.nature_mapper
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
