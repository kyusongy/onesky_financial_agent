# OneSky Financial Agent - Project Documentation

## Overview

A Streamlit-based financial reporting tool that processes Transaction.xlsx files, categorizes transactions into Income/Advance-Settlement/Payable/By Nature/By Province sections, and generates filled Excel reports for OneSky's monthly financial reporting.

---

## Project Structure

```
onesky_financial_agent/
├── app.py                           # Streamlit UI, orchestrates pipeline
├── requirements.txt                 # Dependencies: pandas, openpyxl, streamlit
├── config/
│   └── mappings.py                  # Template constants, row/column mappings
├── src/
│   ├── models.py                    # TransactionGroup, TransactionEntry dataclasses
│   ├── parser.py                    # Excel parsing into TransactionGroups
│   ├── processor.py                 # Transfer splitting, capital row cleaning
│   ├── report_generator.py          # Fills output_template.xlsx
│   ├── validation.py                # ValidationData class for per-row tracking
│   └── calculators/
│       ├── utils.py                 # Exchange rate, amount conversion helpers
│       ├── income.py                # Income section calculation
│       ├── advance_settlement.py    # Advance/Settlement calculation
│       ├── nature.py                # Nature categorization
│       ├── manual_input.py          # Manual account (1250/1230/1252/1500) handling
│       └── province.py              # Province section calculation
└── instruction_data/
    ├── templates/
    │   ├── output_template.xlsx     # Report template
    │   └── dec_final/               # Active lookup tables
    │       ├── Nature.xlsx          # Account → Nature mapping (includes 1230/1250/1252/1500)
    │       ├── Staff_&_Allocation.xlsx  # Staff name → Nature + Province allocation %
    │       └── PIT_SI.xlsx          # Legacy PIT/SI payment entries (no longer used)
    └── test_data/                   # Sample transaction files
```

---

## Template Files (dec_final/)

| File | Sheet | Purpose |
|------|-------|---------|
| Nature.xlsx | Lookup1_AcctList | Account No. → Nature mapping (columns A, B) |
| Staff_&_Allocation.xlsx | Lookup3_Staff & allocation | Staff name → Nature + Province allocation % |
| PIT_SI.xlsx | PIT+SI payment | Legacy PIT/SI entries (no longer used) |

### Nature.xlsx Structure
- Sheet: `Lookup1_AcctList`
- Column A: Account No. (e.g., "71101VN", "1500VN")
- Column B: PACCOM nature

**Special Account Mappings:**
| Account | Logic | Report Section |
|---------|-------|----------------|
| 1230VN | Advance if entry amount > 0, Settlement if < 0 | Row 36/37 |
| 1250VN | Settlement if entry memo contains "settlement", else Advance | Row 36/37 |
| 1252VN | Settlement if entry memo contains "settlement", else Advance | Row 36/37 |
| 1500VN | Payable | Row 38 (Payable) |

### Staff_&_Allocation.xlsx Structure
- Sheet: `Lookup3_Staff & allocation`
- Header row: 1 (0-indexed)
- Column A: Staff Name
- Column B: PACCOM nature (Org/Edu)
- Columns C-K: Province allocations (VNELC, VNDN, VNQN, VNHD, VNQNg, VNHCM, VNLA, VNBN, VNMOET)

### PIT_SI.xlsx Structure (Legacy)
PIT_SI.xlsx is no longer used in province processing. The file may still exist in templates but is ignored by the pipeline.

---

## Workflow & Business Rules

### Step 1: Pre-processing

**Input:** Raw Excel file (Transaction.xlsx)

**Grouping:** Transactions are grouped by empty row separators in the Excel file. Each group becomes a `TransactionGroup` object with:
- Header row (bank row): Contains date, transaction_type, payee_name, bank_amount
- Entry rows: Individual splits with account codes and amounts

**Bank ID Assignment:**
- Bank 29 (USD): Account column contains "USD", "29 bank", or "vietnam (usd)"
- Bank 30 (VND): Account column contains "VND", "30 bank", or "vietnam (vnd)"
- **Other banks (34, etc.):** Ignored completely - only process bank 29 and 30

**Zero Amount Filtering:**
- Groups with `bank_amount = 0` are ignored
- Entries with `amount = 0` or `amount = None` are filtered out from `active_entries`

**Currency Conversion:** For USD transactions, user provides daily exchange rates via UI. Amounts are converted using: `amount / exchange_rate`

**Transfer Edge Case:** If `transaction_type == "Transfer"`, the raw data contains both "In" and "Out" legs. These are split into separate TransactionGroup objects (one per bank). See `processor.py:59-149`.

---

### Step 2: Income Section

Fill this section for Bank 29 and 30 respectively.

| Category | Rule | Code Location |
|----------|------|---------------|
| **Contribution** | `transaction_type == "Deposit" AND name contains "OneSky"` → Sum `bank_amount` | `income.py:49-52` |
| **Fund Transfer to USD** | `memo contains "transfer" AND NOT "ELC"` → Sum amount | `income.py:54-57` |
| **Interest** | `transaction_type == "Deposit" AND memo contains "interest"` → Sum amount | `income.py:64-67` |
| **Cash Settlement** | `memo contains "settlement" AND bank_amount > 0` → Sum amount | `income.py:69-72` |
| **Fund Transfer to ELC** | `memo contains "transfer" AND "elc"` → Sum amount | `income.py:59-62` |

---

### Step 3: Advance/Settlement Section

| Category | Rule | Code Location |
|----------|------|---------------|
| **Advance by Cash** | `header memo contains "advance" AND NOT "settlement"` → Sum **absolute** amount | `advance_settlement.py:41-45` |
| **Settlement** | `header memo contains "settlement" AND bank_amount <= 0` → Sum **original** amount (preserve sign) | `advance_settlement.py:47-51` |

**Note:** Advance/Settlement detection only checks the **header memo (bank_memo)**, not entry memos. This prevents false positives from entries containing "advance" in their memo.

**Note:** Positive settlements (`bank_amount > 0`) go to Income section as "Cash Settlement" instead.

**Additional Source:** Account-based routing from Nature.xlsx:
- 1230VN → Advance if entry amount > 0, Settlement if entry amount < 0
- 1250VN → Settlement if entry memo contains "settlement", otherwise Advance
- 1252VN → Settlement if entry memo contains "settlement", otherwise Advance

---

### Step 4: Payable Section (NEW)

**For 1500 accounts** (nature = "payable" from Nature.xlsx):

| Scenario | Rule | Report Row |
|----------|------|------------|
| Single 1500 entry | Use abs(bank_amount) | Row 38 (Payable) |
| Multi-row with 1500 | Negative 1500: ADD abs(amount) to Payable | Row 38 |
| Multi-row with 1500 | Positive 1500: SUBTRACT amount from Payable | Row 38 |

**Simplified Logic:** Positive 1500 entries only subtract from the Payable section itself, NOT from other nature categories (org/edu/etc.). Non-1500 entries in the same group are processed normally via Nature.xlsx lookup.

**Code Location:** `manual_input.py:300-450`

---

### Step 5: By Nature (Automated & Manual)

**Priority Logic:** If a group was processed in Income, Advance/Settlement, or Payable sections (`is_processed = True`), do NOT double count it here.

#### A. The "Row 13" Capital Cleaning Rule

For any group, if a TransactionEntry has:
- `account_code starts with "13"` (e.g., "1310VN", "1311VN")

Then:
1. Mark that row as `is_ignored`
2. Mark the row immediately preceding it as `is_ignored`

These ignored entries are excluded from `group.active_entries`. See `processor.py:174-204`.

#### B. Nature Lookup Logic

1. Extract account number (e.g., "71101VN") from the Account column
2. Match against `Nature.xlsx` (Sheet: Lookup1_AcctList)
3. Map to normalized categories: `org`, `edu`, `oper`, `nutrition`, `edu_infra`
4. Special accounts (1230/1250/1252/1500) are flagged with `is_manual_trigger = True`

**Nature Normalization Map:**
| Nature.xlsx Value | Internal Code |
|-------------------|---------------|
| Organisational capacity building | org |
| Education quality improvement | edu |
| Program Operation | oper |
| Nutrition for the children | nutrition |
| Education Infrastructure | edu_infra |
| Advance | advance |
| Settlement | settlement |
| Payable | payable |

#### C. Salary/Bonus Processing (Priority Rule)

**After** NatureMapper assigns `nature_type`/`is_manual_trigger` to all entries (including salary/bonus groups), check if the header memo contains "salary" or "bonus". The group is deferred to `ManualInputProcessor._process_salary`, which handles three cases:

| Scenario | Rule | Result |
|----------|------|--------|
| All entries are 1500 | Skip salary processing entirely | Falls through to `_process_by_account_nature` → Payable |
| Has non-1500 entries, staff not found | Skip salary processing | Falls through to account type processing (entries have correct `nature_type` from NatureMapper) |
| Has non-1500 entries, staff found | Split: 1500 entries → Payable, rest → org/edu | Salary amount = sum of non-1500 entry amounts |

**Note:** If salary/bonus memo is present but staff is not found, ManualInputProcessor falls through to account-type processing. Single-entry non-special accounts (e.g., 73209VN) are treated as normal nature and recorded in nature totals.

**Key behavior:** Only non-1500 entries get their `nature_type` overwritten to the staff's nature (org/edu). 1500 entries retain `nature_type = "payable"` as assigned by NatureMapper.

**Code Location:** `nature.py:236-243` (salary/bonus detection, after entry assignment), `manual_input.py:267-338` (three-case processing)

#### D. Reporting Logic (Standard - Non-Manual)

**2-Row Groups (Header + 1 Split):**
- Use the Nature of the entry row
- Report the `bank_amount` (absolute value) under that nature

**Multi-Row Groups (>2 rows):**
- Report the `amount` of each individual entry under its respective Nature
- **Add amounts as-is (preserve sign)** - DO NOT use abs()
- The sum of all entry amounts equals the REVERSE of `bank_amount`

**Code Location:** `nature.py:209-234` - preserves sign, no abs()

---

### Step 6: By Province Section

**Overview:** The province section distributes expenditures by geographic location. Province totals should equal nature section totals (same transactions, different categorization).

**Processing Order:** Province calculation runs AFTER nature section is complete. Only processes groups assigned to NATURE or MANUAL sections (not INCOME, ADVANCE_SETTLEMENT, or PAYABLE).

**Code Location:** `src/calculators/province.py`

#### A. Province Codes (16 total)

```
elc, vnelc, vnhbc, vndn, vnqn, vnhd, vnqng, vnmoet,
vnbd, vnbg, vnla, vnhcm, vnbn, vnother, caobang, province_manual
```

#### B. Ignored Groups

Groups are skipped only when all active entries are `settlement`, `advance`, `cash_settlement`, or `payable`.

**Additional Rule:** Advance/settlement entries are excluded from province totals even when mixed with other entries in a group.

#### C. Province Assignment Logic

**Priority 1: Salary/Bonus Transactions**

If header memo contains "salary" or "bonus":
1. Look up `payee_name` in `Staff_&_Allocation.xlsx` (Sheet: Lookup3_Staff & allocation)
2. Allocation table returns province percentages (e.g., VNDN=0.1, VNELC=0.5, VNMOET=0.4)
3. Distribute sum of non-1500 entry amounts across provinces by percentage
4. If not found in allocation table: Fall through to memo extraction

**Code Location:** `province.py:412-449`

**Priority 2: Province from Memo**

Extract province code from transaction memo:
1. Check entry memos first, then header memo
2. Pattern matching: `VNDN7100102` → `vndn`, `VNDNOTO` → `vndn`
3. Supported patterns: VNELC, VNHBC, VNDN, VNQN, VNHD, VNQNG, VNMOET, VNBD, VNBG, VNLA, VNHCM, VNBN, VNOTHER, CAOBANG, ELC

**Code Location:** `province.py:247-278` (`_extract_province_from_memo`)

**Fallback:** If no province found → `province_manual`

#### D. PIT Lookup Processing (Removed)

There is no PIT/SI lookup in the province section anymore. All province totals come from transaction groups directly.

---

### Output Requirements

#### 1. Processed Transaction File
An Excel file mirroring the input with:
- Manual input groups highlighted in **yellow** (FFFF00)
- Additional columns:
  - `Report_Section`: INCOME / ADVANCE_SETTLEMENT / PAYABLE / NATURE / MANUAL
  - `Type`: Contribution/Fund Transfer/etc. for Income; Org/Edu/etc. for Nature
  - `Amount_in_Respective_Currency`: Converted amount
  - `Is_Processed`: True/False
  - `Exchange_Rate`: Applied rate for USD transactions
  - `Bank`: 29 or 30
  - **29 Validation Columns** (see below)

#### Validation Columns

The processed transaction file includes 29 validation columns (13 nature/income/advance + 16 province) that allow users to verify report totals. When each column is summed (filtered by bank), the total should match the corresponding value in the filled report.

**Nature Section Columns (13):**

| # | Column Header | Internal Key | Report Row |
|---|---------------|--------------|------------|
| 1 | Contribution | contribution | Income - Contribution |
| 2 | Fund transfer to USD account | fund_transfer | Income - Fund Transfer |
| 3 | Interest | interest | Income - Interest |
| 4 | Cash settlement | cash_settlement | Income - Cash Settlement |
| 5 | Fund transfer to ELC | fund_transfer_elc | Income - Fund Transfer ELC |
| 6 | Organisational capacity building | org | By Nature - ORG |
| 7 | Education quality improvement | edu | By Nature - EDU |
| 8 | Program Operation | oper | By Nature - OPER |
| 9 | Nutrition for the children | nutrition | By Nature - NUTRITION |
| 10 | Education Infrastructure | edu_infra | By Nature - EDU_INFRA |
| 11 | Advance by cash | advance | Advance/Settlement - Advance |
| 12 | Settlement | settlement | Advance/Settlement - Settlement |
| 13 | Payable | payable | Payable (Row 38) |

**Province Section Columns (16):**

| # | Column Header | Internal Key | Report Row |
|---|---------------|--------------|------------|
| 14 | ELC Operation | elc | By Province - ELC |
| 15 | ELC training | vnelc | By Province - VNELC |
| 16 | VN General National Training | vnhbc | By Province - VNHBC |
| 17 | Da Nang ICC | vndn | By Province - VNDN |
| 18 | Quang Nam ICC | vnqn | By Province - VNQN |
| 19 | Hai Duong ICC | vnhd | By Province - VNHD |
| 20 | Quang Ngai ICC | vnqng | By Province - VNQNG |
| 21 | Preparation and general of MOET | vnmoet | By Province - VNMOET |
| 22 | Binh Duong MOET | vnbd | By Province - VNBD |
| 23 | Bac Giang MOET | vnbg | By Province - VNBG |
| 24 | Long An MOET | vnla | By Province - VNLA |
| 25 | HCM MOET | vnhcm | By Province - VNHCM |
| 26 | Bac Ninh MOET | vnbn | By Province - VNBN |
| 27 | In-country program support | vnother | By Province - VNOTHER |
| 28 | Cao Bang | caobang | By Province - CAOBANG |
| 29 | Province Manual | province_manual | By Province - Manual |

**Currency Handling:**
- Bank 30 (VND): Values shown in VND (original amounts)
- Bank 29 (USD): Values shown in USD (converted via `amount / exchange_rate`)

**Validation Process:**
1. Filter processed_transaction by Bank = "29" (USD)
2. Sum each validation column
3. Compare against Column E values in filled report
4. Repeat for Bank = "30" (VND) vs Column D

**Code Location:** `src/validation.py` contains the `ValidationData` class that tracks per-row contributions.

#### 2. Final Report
`output_template.xlsx` filled according to the rules above.

**Template Row Mappings:**

| Section | Item | Row (0-based) |
|---------|------|---------------|
| Income | Total | 3 |
| Income | Contribution | 4 |
| Income | Fund Transfer | 5 |
| Income | Interest | 6 |
| Income | Cash Settlement | 7 |
| Income | Fund Transfer ELC | 9 |
| By Nature | Total | 11 |
| By Nature | ORG | 12 |
| By Nature | EDU | 13 |
| By Nature | OPER | 14 |
| By Nature | NUTRITION | 15 |
| By Nature | EDU_INFRA | 16 |
| By Province | Total | 18 |
| By Province | ELC | 19 |
| By Province | VNELC | 20 |
| By Province | VNHBC | 21 |
| By Province | VNDN | 22 |
| By Province | VNQN | 23 |
| By Province | VNHD | 24 |
| By Province | VNQNG | 25 |
| By Province | VNMOET | 26 |
| By Province | VNBD | 27 |
| By Province | VNBG | 28 |
| By Province | VNLA | 29 |
| By Province | VNHCM | 30 |
| By Province | VNBN | 31 |
| By Province | VNOTHER | 32 |
| By Province | CAOBANG | 33 |
| Advance/Settlement | Total | 35 |
| Advance/Settlement | Advance | 36 |
| Advance/Settlement | Settlement | 37 |
| **Payable** | **Payable** | **38** |
| Manual | Nature Manual | 40 |
| Manual | Province Manual | 41 |

**Column Assignment:**
- Column D: VND (Bank 30)
- Column E: USD (Bank 29)

---

## Key Data Models

### TransactionGroup (`src/models.py`)
```python
@dataclass
class TransactionGroup:
    group_id: str
    date: datetime
    bank_identifier: str          # "29" (USD) or "30" (VND)
    transaction_type: str         # "Deposit", "Cheque Expense", "Transfer"
    payee_name: str
    bank_memo: str
    bank_amount: float            # Header row amount
    currency: str
    exchange_rate: float
    entries: list[TransactionEntry]
    is_processed: bool            # Prevents double-counting
    assigned_section: ReportSection
```

### TransactionEntry (`src/models.py`)
```python
@dataclass
class TransactionEntry:
    row_id: str
    account_code: str             # e.g., "71101VN", "1500VN"
    account_name: str
    original_memo: str
    amount: float
    nature_type: str              # org/edu/oper/nutrition/edu_infra/advance/settlement/payable
    is_manual_trigger: bool       # True for 1250/1230/1252/1500 accounts
    is_ignored: bool              # For capital row cleaning
```

### ValidationData (`src/validation.py`)
```python
class ValidationData:
    """Tracks per-row contributions to report columns for validation."""

    row_values: dict[int, dict[str, float]]  # row_index -> {column: amount}

    def set_value(row_index: int, column: str, amount: float) -> None
    def get_value(row_index: int, column: str) -> float | None
```

Used by all calculators to record which rows contribute to which report columns. Passed through the pipeline via `ProcessingResult.validation_data`.

### ProcessingResult (`src/models.py`)
```python
@dataclass
class ProcessingResult:
    """Result of transaction processing."""
    groups_by_bank: dict[str, list[TransactionGroup]]
    income: dict[str, dict[str, float]]              # bank_id -> {contribution, fund_transfer, ...}
    advance_settlement: dict[str, dict[str, float]]  # bank_id -> {advance, settlement}
    payable_totals: dict[str, dict[str, float]]      # bank_id -> {payable}  # NEW
    nature_totals: dict[str, dict[str, float]]       # bank_id -> {org, edu, oper, ...}
    province_totals: dict[str, dict[str, float]]     # bank_id -> {vndn, vnelc, ...}
    manual_groups: list[TransactionGroup]
    all_groups: list[TransactionGroup]
    exchange_rates: dict[str, float]
    validation_data: ValidationData
```

### ReportSection (`src/models.py`)
```python
class ReportSection(Enum):
    INCOME = "Income"
    ADVANCE_SETTLEMENT = "Advance_Settlement"
    PAYABLE = "Payable"  # NEW
    NATURE = "Nature"
    MANUAL = "Manual"
    IGNORE = "Ignore"
```

---

## Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run Streamlit app
streamlit run app.py
```

The application will be available at `http://localhost:8501`

---

## Processing Pipeline

```
1. Parse Transaction.xlsx
   └─> TransactionGroup + TransactionEntry objects

2. Process Transactions
   ├─> Split transfer groups (In/Out legs)
   └─> Remove capital rows (Row 13 rule)

3. Create ValidationData
   └─> Initialize empty tracker for per-row contributions

4. Calculate Income (tracks validation data)
   └─> Deposit+OneSky, Transfer, Interest, Cash Settlement (positive settlement)
   └─> Records header row contributions to income columns

5. Calculate Advance/Settlement (tracks validation data)
   └─> Advance, Settlement (negative only) memos
   └─> Records header row contributions to advance/settlement columns

6. Map Nature Categories (NatureMapper, tracks validation data)
   ├─> Assign nature_type to ALL entries first (including salary/bonus groups)
   ├─> Lookup accounts in Nature.xlsx (Lookup1_AcctList sheet)
   ├─> Mark manual triggers (1250/1230/1252/1500)
   ├─> THEN check salary/bonus memo → defer to ManualInputProcessor (entries already typed)
   └─> Records header or entry row contributions to nature columns

7. Mark Processed Groups
   └─> Prevent double-counting

8. Process Manual Input (ManualInputProcessor, tracks validation data)
   ├─> Priority 1: Salary/Bonus check (memo contains "salary" or "bonus")
   │   ├─> All entries 1500 → skip salary, fall through to payable
   │   ├─> Staff not found → fall through to account type processing
   │   └─> Staff found → split: 1500 entries → payable, rest → org/edu
   ├─> Priority 2: Account type processing based on entry-level logic
   │   ├─> 1230VN → Advance if entry amount > 0, Settlement if < 0
   │   ├─> 1250VN → Settlement if memo contains "settlement", else Advance
   │   ├─> 1252VN → Settlement if memo contains "settlement", else Advance
   │   └─> 1500VN → Payable section
   ├─> Handle 1500 offset logic (positive 1500 subtracts from Payable only)
   └─> Records row contributions to respective sections

9. Calculate Province Totals (ProvinceMapper, tracks validation data)
   ├─> Only process NATURE and MANUAL section groups
   ├─> Skip groups only if all entries are settlement/advance/cash_settlement/payable
   ├─> Priority 1: Salary/Bonus → Staff_&_Allocation.xlsx percentages
   ├─> Priority 2: Extract province from memo
   └─> Records province validation columns

10. Generate Report
    ├─> Fill output_template.xlsx (Income, Advance/Settlement, Payable, Nature, Province)
    ├─> Create processed transaction file with highlights
    └─> Populate 29 validation columns from ValidationData
```

---

## Important Notes

- **Sign Convention:** Entry amounts are added as-is (preserve sign). The sum of entries equals the reverse of bank_amount.
- **Settlement Sign:** Settlement uses original bank_amount (preserves sign), while Advance uses absolute value.
- **1500 Handling (Simplified):** Positive 1500 amounts are SUBTRACTED from the Payable section only, not from other nature categories.
- **Salary/Bonus Priority:** Transactions with "salary" or "bonus" in memo are processed via staff lookup BEFORE account type processing. However, 1500 (payable) entries within salary/bonus groups are NOT overridden — they remain payable and are split out separately. Only non-1500 entries get the staff's nature (org/edu). If all entries are 1500, salary processing is skipped entirely.
- **Double-Counting Prevention:** Groups are marked as `is_processed` after being categorized to avoid counting in multiple sections.
- **Manual Review:** Groups that cannot be automatically processed are highlighted yellow for manual review.
- **Validation Columns:** The 29 validation columns (13 nature/income + 16 province) in the processed transaction file enable users to verify report totals by summing each column (filtered by bank).
- **Province = Nature Total:** Province section total should equal nature section total (same transactions categorized differently).
- **PIT/SI/HI Groups:** No special PIT/SI/HI handling in province processing.
- **Province Allocation:** For salary/bonus, staff may have split allocations across multiple provinces (dynamically read from Staff_&_Allocation.xlsx columns).
- **Payable Section:** 1500 accounts now have their own dedicated section (row 38) separate from Advance/Settlement.
