# OneSky Financial Report Automation

A Streamlit-based tool for automating monthly financial report generation from transaction files.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
streamlit run app.py
```

Open your browser at `http://localhost:8501`

## How to Use

1. **Upload** your Transaction.xlsx file
2. **Enter exchange rate** if USD transactions are detected
3. **Click Process** to analyze transactions
4. **Download** the filled report and marked transaction file

## What It Does

- Parses transaction files and groups by date
- Separates USD (Bank 29) and VND (Bank 30) transactions
- Calculates Income: Contribution, Fund Transfer, Interest, PED
- Calculates Advance/Settlement amounts
- Maps expenses to nature categories using lookup table
- Fills the output template with calculated values
- Highlights transactions requiring manual review

## Project Structure

```
├── app.py                 # Main Streamlit application
├── requirements.txt       # Python dependencies
├── config/
│   └── mappings.py        # Template row/column mappings
└── src/
    ├── models.py          # Data classes
    ├── parser.py          # Transaction file parsing
    ├── processor.py       # Grouping and preprocessing
    ├── report_generator.py
    └── calculators/
        ├── income.py
        ├── advance_settlement.py
        └── nature.py
```

## Requirements

- Python 3.10+
- pandas
- openpyxl
- streamlit

