# CureClaim — AI-Powered Hospital Bill Auditor

**Your Hospital Bill. Audited With Evidence.**

CureClaim is a full-stack Django web application that extracts hospital bill line items, checks financial calculations using deterministic `Decimal` arithmetic, evaluates structured billing rules, and generates downloadable PDF audit reports.

---

## 1. Core Architectural Principle

> **AI reads and interprets documents. Deterministic code performs calculations. Explicitly sourced rules support audit findings. Humans review uncertain or consequential conclusions.**

CureClaim never asks an LLM to invent financial rules, compute totals, or accuse a hospital of fraud.

---

## 2. Key Features

- **Healthcare-Fintech SaaS Dashboard**: Live database KPIs (Total Bills Audited, Potential Findings, Amount Requiring Review, Bills Awaiting Verification) and quick-launch workflows.
- **Interactive Synthetic Demo (`DEMO-001`)**: Works completely offline without an API key. Audits a synthetic ₹8,500 bill from *Northstar General Hospital* and flags a ₹500 *Surgical gloves* consumable under an explicitly marked `DEMO ONLY — NOT OFFICIAL GUIDANCE` package-inclusion rule.
- **Manual Bill Entry**: Dynamic line-item table with live client-side subtotals and strict server-side `Decimal` recalculation.
- **Optional Gemini Multimodal Extraction**: Validates uploaded JPG, PNG, and PDF files on the server (using `Pillow` and `pypdf`), requires explicit user disclosure/consent before calling the configured `google-genai` model, and presents an interactive verification screen highlighting uncertain fields before auditing.
- **Deterministic Financial & Rule Engine**:
  1. Line-item multiplication checks (`Quantity × Unit Price == Billed Line Total`).
  2. Potential duplicate line-item detection (flagged for clinical timestamp verification).
  3. Grand total reconciliation against summed line items and documented adjustments.
  4. Missing/invalid data & low-confidence optical extraction flags.
  5. Structured database rule evaluation (`SOURCE_VERIFIED`, `DEMO_ONLY`, and `UNVERIFIED` categories) with automatic per-item financial-impact deduplication to prevent double-counting.
- **Downloadable PDF Audit Report**: Generates a multi-section PDF report using `ReportLab` directly from saved audit records.

---

## 3. Windows Setup & Run Instructions (VS Code + PowerShell)

Open PowerShell in VS Code inside the `cureclaim` directory and run:

```powershell
# 1. Create a Python virtual environment
py -m venv .venv

# 2. Activate the virtual environment
# Note: If PowerShell blocks script execution, run this temporary process-scoped command first:
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env configuration file
Copy-Item .env.example .env

# 5. Create and apply SQLite database migrations
python manage.py makemigrations auditor
python manage.py migrate

# 6. Seed the synthetic demo bill (DEMO-001) and sample rules
python manage.py seed_demo

# 7. Run the automated test suite (13 tests covering math, rules, mocked AI, and views)
python manage.py test

# 8. Start the local development server
python manage.py runserver
```

> **Alternative if PowerShell activation is restricted:** You can invoke the virtual environment's Python binary directly without activating:
> `.\.venv\Scripts\python.exe manage.py runserver`

Open **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)** in your browser.

---

## 4. Configuring Optional Gemini AI Extraction

CureClaim works out of the box without an API key in **Demo & Manual Mode**. To enable live AI extraction for uploaded JPG/PNG/PDF bills:

1. Open `.env` in VS Code.
2. Set your API key and preferred model:
   ```ini
   GEMINI_API_KEY=your_actual_gemini_api_key_here
   GEMINI_MODEL=gemini-2.5-flash
   ```
3. Restart `python manage.py runserver`. The top navigation bar will switch from **DEMO MODE** to **AI Extraction Active**.

---

## 5. Known Limitations & Disclaimers

- CureClaim is an informational billing-review aid for hackathon demonstration and educational use. It is **not** a medical diagnosis tool, a legal compliance system, or a guarantee of refund/reimbursement.
- Reference or scheme tariffs (such as CGHS or insurance packages) depend on hospital empanelment, patient eligibility, city tier, and room category; CureClaim never treats reference rates as universal price caps for private hospitals.