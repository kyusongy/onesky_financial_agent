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
from src.report_generator import ReportGenerator, generate_marked_transactions
from src.models import ProcessingResult, BankType

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
</style>
""", unsafe_allow_html=True)


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
        
        # Optional: Custom nature lookup table
        st.markdown("### ⚙️ Optional Settings")
        nature_lookup_file = st.file_uploader(
            "Custom Nature Lookup Table",
            type=["xlsx"],
            help="Optional: Upload a custom nature lookup table to override the default",
            key="nature_upload"
        )
        
        if nature_lookup_file:
            st.success("✓ Custom lookup table loaded")
    
    # Main content area
    if transaction_file is None:
        st.info("👈 Please upload a transaction file to begin processing")
        
        # Show instructions
        with st.expander("📖 How to use this tool"):
            st.markdown("""
            1. **Upload Transaction File**: Select your monthly transaction Excel file
            2. **Enter Exchange Rate**: If USD transactions exist, enter the VND to USD exchange rate
            3. **Process**: Click the process button to analyze transactions
            4. **Download Results**: Download the filled report and marked transaction file
            
            **Supported Features:**
            - Income calculation (Contribution, Fund Transfer, Interest, PED)
            - Advance/Settlement tracking
            - Expenditure by nature categorization
            - Manual review highlighting for complex transactions
            """)
        return
    
    # Store transaction bytes for later use
    st.session_state.transaction_bytes = transaction_file.read()
    transaction_file.seek(0)  # Reset for processing
    
    # Parse transaction file to check for USD transactions
    with st.spinner("Analyzing transaction file..."):
        try:
            groups, has_usd = parse_transaction_file(BytesIO(st.session_state.transaction_bytes))
            st.success(f"✓ Loaded {len(groups)} transaction groups")
        except Exception as e:
            st.error(f"Error parsing transaction file: {str(e)}")
            return
    
    # Exchange rate input if USD transactions exist
    exchange_rate = None
    if has_usd:
        st.markdown('<p class="section-header">💱 Exchange Rate</p>', unsafe_allow_html=True)
        st.markdown('<div class="warning-box">USD transactions detected. Please enter the exchange rate.</div>', unsafe_allow_html=True)
        
        col1, col2 = st.columns([1, 2])
        with col1:
            exchange_rate = st.number_input(
                "VND to USD Rate",
                min_value=1.0,
                max_value=100000.0,
                value=25000.0,
                step=100.0,
                help="Enter the exchange rate (e.g., 25000 means 1 USD = 25,000 VND)"
            )
    
    # Process button
    st.markdown('<p class="section-header">🔄 Processing</p>', unsafe_allow_html=True)
    
    if st.button("🚀 Process Transactions", type="primary", use_container_width=True):
        with st.spinner("Processing transactions..."):
            try:
                # Step 1: Parse transactions
                groups, has_usd = parse_transaction_file(BytesIO(st.session_state.transaction_bytes))
                
                # Step 2: Process groups (split transfers, remove capital rows)
                groups_by_bank = process_transactions(groups, exchange_rate)
                
                # Step 3: Calculate income
                income = calculate_income(groups_by_bank)
                
                # Step 4: Calculate advance/settlement
                advance_settlement = calculate_advance_settlement(groups_by_bank)
                
                # Step 5: Map nature categories
                nature_mapper = NatureMapper(
                    BytesIO(nature_lookup_file.read()) if nature_lookup_file else None
                )
                if nature_lookup_file:
                    nature_lookup_file.seek(0)
                
                nature_totals, manual_groups = nature_mapper.process_groups(groups_by_bank)
                
                # Create processing result
                result = ProcessingResult(
                    groups_by_bank=groups_by_bank,
                    income=income,
                    advance_settlement=advance_settlement,
                    nature_totals=nature_totals,
                    manual_groups=manual_groups,
                    exchange_rate=exchange_rate,
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
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            total_groups = sum(len(g) for g in result.groups_by_bank.values())
            st.metric("Total Groups Processed", total_groups)
        
        with col2:
            st.metric("Manual Review Items", len(result.manual_groups))
        
        with col3:
            if result.exchange_rate:
                st.metric("Exchange Rate", f"{result.exchange_rate:,.0f}")
            else:
                st.metric("Exchange Rate", "N/A")
        
        # Detailed breakdown
        with st.expander("📊 Detailed Breakdown", expanded=True):
            tab1, tab2, tab3 = st.tabs(["Income", "Advance/Settlement", "By Nature"])
            
            with tab1:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**VND Bank (30)**")
                    for key, val in result.income[BankType.VND].items():
                        if val != 0:
                            st.write(f"- {key.replace('_', ' ').title()}: {val:,.0f}")
                with col2:
                    st.markdown("**USD Bank (29)**")
                    for key, val in result.income[BankType.USD].items():
                        if val != 0:
                            st.write(f"- {key.replace('_', ' ').title()}: {val:,.0f}")
            
            with tab2:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**VND Bank (30)**")
                    for key, val in result.advance_settlement[BankType.VND].items():
                        if val != 0:
                            st.write(f"- {key.title()}: {val:,.0f}")
                with col2:
                    st.markdown("**USD Bank (29)**")
                    for key, val in result.advance_settlement[BankType.USD].items():
                        if val != 0:
                            st.write(f"- {key.title()}: {val:,.0f}")
            
            with tab3:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**VND Bank (30)**")
                    for key, val in result.nature_totals[BankType.VND].items():
                        if val != 0:
                            st.write(f"- {key.upper()}: {val:,.0f}")
                with col2:
                    st.markdown("**USD Bank (29)**")
                    for key, val in result.nature_totals[BankType.USD].items():
                        if val != 0:
                            st.write(f"- {key.upper()}: {val:,.0f}")
        
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
            if result.manual_groups:
                try:
                    marked_bytes = generate_marked_transactions(
                        BytesIO(st.session_state.transaction_bytes),
                        result.manual_groups
                    )
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    st.download_button(
                        label="📋 Download Marked Transactions",
                        data=marked_bytes,
                        file_name=f"marked_transactions_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Error generating marked file: {str(e)}")
            else:
                st.info("No transactions require manual review")


if __name__ == "__main__":
    main()

