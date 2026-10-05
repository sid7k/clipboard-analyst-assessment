import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
DECISIONS_FILE = OUTPUT_DIR / "decisions.json"
EXECUTION_STATE_FILE = OUTPUT_DIR / "execution_state.json"

DEFAULT_API_BASE = (
    "https://analyst-assessment-production.up.railway.app/api/v1"
)

WRITABLE_FIELDS = {
    "name",
    "parent_id",
    "billing_street",
    "billing_city",
    "billing_state",
    "billing_zip",
    "care_type",
    "status",
    "phone",
    "chow_current_account",
    "duplicate_of_account",
    "note",
}

FINAL_DECISIONS = {"APPLIED", "REJECTED"}


class CRMWriteError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(path)


def _load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def load_decisions():
    data = _load_json(DECISIONS_FILE, {"decisions": []})
    if isinstance(data, list):
        return {"decisions": data}
    if not isinstance(data, dict):
        return {"decisions": []}
    data.setdefault("decisions", [])
    return data


def load_execution_state():
    data = _load_json(EXECUTION_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_execution_state(data):
    _atomic_json_write(EXECUTION_STATE_FILE, data)


def has_crm_token():
    load_dotenv(ROOT / ".env")
    return bool(os.getenv("CRM_TOKEN", "").strip())


def _api_base():
    load_dotenv(ROOT / ".env")
    return os.getenv("CRM_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _headers():
    load_dotenv(ROOT / ".env")
    token = os.getenv("CRM_TOKEN", "").strip()
    if not token:
        raise CRMWriteError(
            "CRM_TOKEN is missing. Add it to .env before applying approved changes."
        )

    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(method, path, payload=None):
    url = f"{_api_base()}/{path.lstrip('/')}"
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=_headers(),
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise CRMWriteError(f"CRM request failed: {exc}") from exc

    if not response.ok:
        body = (response.text or "").strip()
        if len(body) > 1200:
            body = body[:1200] + "..."
        raise CRMWriteError(
            f"CRM API returned HTTP {response.status_code}"
            + (f": {body}" if body else "")
        )

    if response.status_code == 204 or not (response.text or "").strip():
        return {}

    try:
        return response.json()
    except ValueError:
        return {"raw_response": response.text}


def _writable_payload(values):
    return {
        key: value
        for key, value in (values or {}).items()
        if key in WRITABLE_FIELDS and value != "__NEW_ACCOUNT_ID__"
    }


def _extract_account_id(payload):
    candidates = [payload]
    if isinstance(payload, dict):
        for key in ("data", "account", "result"):
            value = payload.get(key)
            if isinstance(value, dict):
                candidates.append(value)

    for item in candidates:
        if isinstance(item, dict):
            account_id = item.get("account_id") or item.get("id")
            if account_id:
                return str(account_id)

    raise CRMWriteError(
        "Account creation succeeded but the response did not contain an account_id."
    )


def create_account(fields):
    payload = _writable_payload(fields)
    if not payload.get("name"):
        raise CRMWriteError("Cannot create an account without a name.")
    response = _request("POST", "/accounts", payload)
    return _extract_account_id(response), response


def patch_account(account_id, changes):
    if not account_id:
        raise CRMWriteError("Cannot update CRM account: account_id is missing.")

    payload = _writable_payload(changes)
    if not payload:
        raise CRMWriteError("No writable CRM fields were supplied for this update.")

    response = _request("PATCH", f"/accounts/{account_id}", payload)
    return response


def record_decision(
    proposal,
    decision,
    case_key,
    case_title,
    result=None,
):
    decision = decision.upper()
    if decision not in FINAL_DECISIONS:
        raise ValueError(f"Unsupported final decision: {decision}")

    proposal_id = proposal.get("proposal_id")
    if not proposal_id:
        raise ValueError("Proposal has no proposal_id.")

    data = load_decisions()
    rows = data["decisions"]

    # One final row per proposal. Replace an older non-final/legacy row if present.
    rows = [
        row
        for row in rows
        if row.get("proposal_id") != proposal_id
    ]

    rows.append(
        {
            "proposal_id": proposal_id,
            "decision": decision,
            "action": proposal.get("action"),
            "case_key": case_key,
            "case_title": case_title,
            "decided_at": utc_now(),
            "result": result or {},
        }
    )

    data["decisions"] = rows
    _atomic_json_write(DECISIONS_FILE, data)


def reject_case(proposals, case_key, case_title):
    for proposal in proposals:
        record_decision(
            proposal=proposal,
            decision="REJECTED",
            case_key=case_key,
            case_title=case_title,
            result={"crm_write": False},
        )


def _apply_create(proposal):
    proposal_id = proposal["proposal_id"]
    state = load_execution_state()
    proposal_state = state.get(proposal_id, {})

    created_id = proposal_state.get("created_account_id")

    # Idempotency protection: if a previous run already created the account
    # but crashed before recording the final decision, reuse that ID.
    if not created_id:
        created_id, response = create_account(proposal.get("create_fields") or {})
        state[proposal_id] = {
            "action": "CREATE_ACCOUNT",
            "created_account_id": created_id,
            "created_at": utc_now(),
        }
        save_execution_state(state)
    else:
        response = {"reused_created_account_id": created_id}

    return {
        "created_account_id": created_id,
        "api_response": response,
    }


def _apply_chow(proposal):
    old_account = proposal.get("crm_account") or {}
    old_account_id = old_account.get("account_id")

    revenue = float(old_account.get("lifetime_revenue") or 0)
    ar = float(old_account.get("outstanding_ar") or 0)

    # Guard the exact Clipboard CHOW condition at write time too.
    if not (revenue > 0 and ar > 0):
        raise CRMWriteError(
            "CHOW write blocked: this proposal no longer satisfies the "
            "revenue > 0 AND outstanding AR > 0 condition."
        )

    proposal_id = proposal["proposal_id"]
    state = load_execution_state()
    proposal_state = state.get(proposal_id, {})

    new_account_id = proposal_state.get("created_account_id")

    if not new_account_id:
        new_account_id, create_response = create_account(
            proposal.get("create_fields") or {}
        )
        state[proposal_id] = {
            "action": "CHOW_CREATE_NEW",
            "created_account_id": new_account_id,
            "created_at": utc_now(),
            "old_account_id": old_account_id,
            "stage": "NEW_ACCOUNT_CREATED_PENDING_OLD_LINK",
        }
        # Persist immediately so a retry cannot create a second current account.
        save_execution_state(state)
    else:
        create_response = {
            "reused_created_account_id": new_account_id
        }

    # Clipboard requirement: preserve the old account and change ONLY the CHOW link.
    patch_response = patch_account(
        old_account_id,
        {"chow_current_account": new_account_id},
    )

    state = load_execution_state()
    state[proposal_id] = {
        **state.get(proposal_id, {}),
        "stage": "COMPLETE",
        "completed_at": utc_now(),
    }
    save_execution_state(state)

    return {
        "created_account_id": new_account_id,
        "old_account_id": old_account_id,
        "create_response": create_response,
        "old_account_patch_response": patch_response,
    }


def apply_proposal(proposal):
    action = proposal.get("action")

    if action in {"UPDATE_ACCOUNT", "MARK_DUPLICATE", "MARK_NEEDS_REVIEW"}:
        account = proposal.get("crm_account") or {}
        response = patch_account(
            account.get("account_id"),
            proposal.get("changes") or {},
        )
        return {
            "updated_account_id": account.get("account_id"),
            "api_response": response,
        }

    if action == "CREATE_ACCOUNT":
        return _apply_create(proposal)

    if action == "CHOW_CREATE_NEW":
        return _apply_chow(proposal)

    raise CRMWriteError(f"Unsupported proposal action: {action}")


def apply_case(proposals, case_key, case_title):
    """
    Apply one business case in a safe order.

    The API does not expose a multi-account transaction, so each successfully
    completed action is recorded immediately. If a later action fails, the
    failed/unstarted proposals remain pending instead of falsely marking the
    entire case complete.
    """
    action_order = {
        "CHOW_CREATE_NEW": 10,
        "CREATE_ACCOUNT": 10,
        "UPDATE_ACCOUNT": 10,
        "MARK_NEEDS_REVIEW": 20,
        "MARK_DUPLICATE": 30,
    }

    ordered = sorted(
        proposals,
        key=lambda p: (
            action_order.get(p.get("action"), 99),
            p.get("proposal_id", ""),
        ),
    )

    completed = []

    for proposal in ordered:
        result = apply_proposal(proposal)
        record_decision(
            proposal=proposal,
            decision="APPLIED",
            case_key=case_key,
            case_title=case_title,
            result=result,
        )
        completed.append(
            {
                "proposal_id": proposal.get("proposal_id"),
                "action": proposal.get("action"),
                "result": result,
            }
        )

    return completed
