# Clipboard Analyst Assessment

This project reconciles Bellhaven Senior Living's website locations with CRM accounts, identifies discrepancies, presents proposed changes for human review, and applies only approved changes through the CRM API.

## Final Result

After completing the reconciliation and rerunning the workflow:

- Website facilities: 34
- Matched facilities: 34
- Missing facilities: 0
- Ambiguous facilities: 0
- Pending CRM actions: 0
- Final result: 34 CONFIDENT_MATCH

The workflow was rerun after the CRM updates and produced no additional proposals.

## Workflow

```text
Bellhaven website
    ↓
scraper.py

CRM API
    ↓
crm.py

Website + CRM data
    ↓
matching.py
    ↓
proposals.py
    ↓
app.py
Human review
    ↓
crm_writer.py
    ↓
CRM API
```

## Main Components

### `scraper.py`

Scrapes all Bellhaven community pages and collects:

- facility name
- address
- city
- state
- ZIP code
- care offerings
- source URL

### `crm.py`

Downloads the latest CRM accounts through the provided API with pagination.

### `matching.py`

Matches website facilities to CRM accounts using normalized names, addresses, city, state, ZIP code, and other supporting evidence.

Safe formatting differences such as `Street` / `St`, `Avenue` / `Ave`, and `Drive` / `Dr` may be normalized for matching.

Ambiguous identity cases can be sent for human review rather than guessed automatically.

### `proposals.py`

Generates CRM change proposals after matching.

Supported actions include:

- `UPDATE_ACCOUNT`
- `CREATE_ACCOUNT`
- `MARK_DUPLICATE`
- `MARK_NEEDS_REVIEW`
- `CHOW_CREATE_NEW`

### `app.py`

Streamlit review interface.

The reviewer can inspect website evidence, existing CRM information, proposed changes, duplicate relationships, and CHOW information before approving or rejecting a case.

CRM changes are never applied without human approval.

### `crm_writer.py`

Applies reviewer-approved changes through the CRM API and records completed decisions for safe reruns.

## Duplicate Handling

For duplicate accounts, losing records are:

```text
status = Inactive
duplicate_of_account = surviving account ID
```

Clipboard specifies how the losing duplicate should be handled but does not specify which matching record must survive.

The review interface therefore allows the reviewer to confirm or change the PRIMARY account before approval.

## CHOW Handling

Before re-parenting an account, the workflow checks:

```text
lifetime_revenue
outstanding_ar
```

If both are greater than zero:

1. The historical account is preserved under its existing parent.
2. A new current Bellhaven account is created.
3. `chow_current_account` on the historical account is set to the new account ID.

This prevents historical revenue and outstanding AR from being incorrectly moved to a new ownership record.

## Rerun Safety

Human decisions and completed CRM actions are persisted.

Running the reconciliation again does not recreate already completed proposals.

The final full rerun resulted in:

```text
Website facilities: 34
Matched facilities: 34
Missing in CRM: 0
Pending human-review facilities: 0
CONFIDENT_MATCH: 34
Proposals: 0
```

## Daily Automation

Run manually with:

```powershell
python run_daily.py
```

The daily process runs:

1. website scraping
2. CRM fetch
3. matching
4. proposal generation

It does not automatically approve or write CRM changes.

`run_daily.bat` runs the workflow using the project virtual environment.

`schedule_daily.ps1` creates a Windows Scheduled Task that runs daily at 06:00.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` from `.env.example`:

```text
CRM_TOKEN=your_token_here
CRM_API_BASE=https://analyst-assessment-production.up.railway.app/api/v1
```

Never commit the real CRM token.

Run the review application with:

```powershell
streamlit run app.py
```

## AI Usage

I used AI assistance for code drafting, debugging, refactoring, reasoning through edge cases, review-interface development, and documentation.

I reviewed the implementation, manually reviewed the CRM proposals, applied the intended changes, and verified the final CRM state through the provided API.

## Future Improvements

With more time I would add:

- automated matching tests
- mocked CRM API tests
- structured logging
- richer decision-history reporting
- cross-platform scheduling

I would keep human approval for CRM writes because duplicate, ownership, and CHOW decisions can have meaningful business consequences.

## Time Spent

Approximately 3–4 hours of active work in total, including about 1.5 hours on the core reconciliation workflow and the remaining time on the review UI, validation, and usability improvements.