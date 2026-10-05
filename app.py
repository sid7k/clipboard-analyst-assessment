import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import streamlit as st

from crm_writer import (
    CRMWriteError,
    apply_case,
    has_crm_token,
    load_decisions,
    reject_case,
)

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"

CRM_FILE = INPUT_DIR / "crm_accounts.json"
MATCH_REVIEW_FILE = INPUT_DIR / "match_review.csv"
MATCHES_FILE = OUTPUT_DIR / "matches.json"
PROPOSALS_FILE = OUTPUT_DIR / "proposals.json"
REVIEW_SETTINGS_FILE = OUTPUT_DIR / "review_settings.json"

CRM_SCRIPT = ROOT / "crm.py"
MATCHING_SCRIPT = ROOT / "matching.py"
PROPOSALS_SCRIPT = ROOT / "proposals.py"

REVIEW_COLUMNS = [
    "website_source_url",
    "website_name",
    "website_address",
    "website_city",
    "website_state",
    "website_zip",
    "crm_account_id",
    "crm_name",
    "crm_address",
    "crm_city",
    "crm_state",
    "crm_zip",
    "crm_parent",
    "reason",
    "name_similarity",
    "address_similarity",
    "decision",
    "note",
]


def load_json(path, default=None):
    if default is None:
        default = []
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_review_settings():
    default = {"duplicate_survivor_policy": "automatic"}

    if not REVIEW_SETTINGS_FILE.exists():
        return default

    try:
        with open(REVIEW_SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default

    policy = saved.get("duplicate_survivor_policy", "automatic")
    if policy not in {"automatic", "manual"}:
        policy = "automatic"

    return {"duplicate_survivor_policy": policy}


def save_review_settings(settings):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def load_review_rows():
    if not MATCH_REVIEW_FILE.exists():
        return []

    with open(MATCH_REVIEW_FILE, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def save_review_rows(rows):
    with open(MATCH_REVIEW_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def run_script(path):
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    output = (result.stdout or "") + (result.stderr or "")

    if result.returncode != 0:
        raise RuntimeError(output.strip() or f"{path.name} failed")

    return output.strip()


def refresh_matching_and_proposals():
    matching_output = run_script(MATCHING_SCRIPT)
    proposals_output = run_script(PROPOSALS_SCRIPT)
    return matching_output, proposals_output


def refresh_after_crm_write():
    """
    Pull the CRM again, then rebuild matches and proposals from fresh state.
    """
    if not CRM_SCRIPT.exists():
        raise RuntimeError(
            "crm.py is missing. CRM was changed, but the local snapshot "
            "cannot be refreshed automatically."
        )

    crm_output = run_script(CRM_SCRIPT)
    matching_output = run_script(MATCHING_SCRIPT)
    proposals_output = run_script(PROPOSALS_SCRIPT)

    return crm_output, matching_output, proposals_output


def refresh_after_reject():
    """
    Rebuild proposals so newly rejected proposal IDs disappear immediately.
    No CRM fetch is required because Reject performs no CRM write.
    """
    return run_script(PROPOSALS_SCRIPT)


def duplicate_case_human_confirmed(group):
    website = group.get("website") or {}
    source_url = website.get("source_url", "")
    if not source_url:
        return False

    primary_rows = [
        row
        for row in load_review_rows()
        if (
            row.get("website_source_url", "") == source_url
            and row.get("decision", "").strip().upper() == "PRIMARY"
        )
    ]
    return len(primary_rows) == 1


def decision_counts():
    data = load_decisions()
    rows = data.get("decisions", []) if isinstance(data, dict) else []
    counts = Counter(
        str(row.get("decision", "")).upper()
        for row in rows
        if isinstance(row, dict)
    )
    return counts


def selected_rows_from_event(event):
    """Return selected dataframe row indexes across Streamlit state shapes."""
    try:
        return list(event.selection.rows)
    except Exception:
        try:
            return list(event["selection"]["rows"])
        except Exception:
            return []


def money(value):
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def candidate_label(account):
    parent = account.get("parent_name") or "No parent"
    return f"{account.get('name', 'Unnamed')} | {parent}"


def render_website_record(website):
    st.markdown("#### Website evidence")
    col1, col2 = st.columns(2)

    with col1:
        st.write("**Facility**")
        st.write(website.get("name", ""))
        st.write("**Address**")
        st.write(
            f"{website.get('address', '')}, "
            f"{website.get('city', '')}, "
            f"{website.get('state', '')} "
            f"{website.get('zip', '')}"
        )

    with col2:
        st.write("**Care offerings**")
        care = website.get("care_offerings", [])
        st.write(", ".join(care) if care else "—")
        st.write("**Source**")
        source_url = website.get("source_url", "")
        if source_url:
            st.link_button("Open website record", source_url)


def render_candidate(account, review_row):
    st.markdown(f"### {account.get('name', 'Unnamed account')}")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.write("**CRM account ID**")
        st.code(account.get("account_id", ""), language=None)

        st.write("**Address**")
        st.write(
            f"{account.get('billing_street', '')}, "
            f"{account.get('billing_city', '')}, "
            f"{account.get('billing_state', '')} "
            f"{account.get('billing_zip', '')}"
        )

    with c2:
        st.write("**Parent**")
        st.write(account.get("parent_name") or "No parent")

        st.write("**Care type**")
        st.write(account.get("care_type") or "—")

    with c3:
        st.write("**Lifetime revenue**")
        st.write(money(account.get("lifetime_revenue")))

        st.write("**Outstanding AR**")
        st.write(money(account.get("outstanding_ar")))

    if review_row:
        st.caption(
            f"Why surfaced: {review_row.get('reason', '—')} | "
            f"Name similarity: {review_row.get('name_similarity', '—')} | "
            f"Address similarity: {review_row.get('address_similarity', '—')}"
        )


def update_grouped_match_decision(
    website,
    candidate_ids,
    primary_id,
    secondary_decisions,
    note,
    candidate_accounts=None,
):
    """
    Save an explicit human match decision.

    For previously ambiguous cases, rows already exist in match_review.csv.
    For auto-resolved duplicate cases, missing rows are created here so the
    human override becomes durable and wins on future matching runs.
    """
    rows = load_review_rows()
    source_url = website.get("source_url", "")
    candidate_accounts = candidate_accounts or {}

    row_lookup = {
        (row.get("website_source_url", ""), row.get("crm_account_id", "")): row
        for row in rows
    }

    for candidate_id in candidate_ids:
        key = (source_url, candidate_id)

        if key not in row_lookup:
            account = candidate_accounts.get(candidate_id)

            if not account:
                raise ValueError(
                    f"CRM account {candidate_id} is missing from review context."
                )

            new_row = {
                "website_source_url": source_url,
                "website_name": website.get("name", ""),
                "website_address": website.get("address", ""),
                "website_city": website.get("city", ""),
                "website_state": website.get("state", ""),
                "website_zip": website.get("zip", ""),
                "crm_account_id": candidate_id,
                "crm_name": account.get("name", ""),
                "crm_address": account.get("billing_street", ""),
                "crm_city": account.get("billing_city", ""),
                "crm_state": account.get("billing_state", ""),
                "crm_zip": account.get("billing_zip", ""),
                "crm_parent": account.get("parent_name", ""),
                "reason": "Human override from Duplicate Resolution review",
                "name_similarity": "",
                "address_similarity": "",
                "decision": "",
                "note": "",
            }

            rows.append(new_row)
            row_lookup[key] = new_row

    for candidate_id in candidate_ids:
        row = row_lookup[(source_url, candidate_id)]

        if primary_id == "__NONE__":
            row["decision"] = "NOT_MATCH"
        elif candidate_id == primary_id:
            row["decision"] = "PRIMARY"
        else:
            row["decision"] = secondary_decisions[candidate_id]

        row["note"] = note.strip()

    save_review_rows(rows)

def render_match_review(matches, crm_accounts):
    pending = [
        item
        for item in matches
        if item.get("status") in {"PENDING_HUMAN_REVIEW", "REVIEW_ERROR"}
    ]

    st.subheader("Match Review")
    st.caption(
        "Ambiguous facilities are grouped into review cases. Select a case from "
        "the inbox, compare the related CRM candidates, then save one grouped decision."
    )

    if not pending:
        st.success("All ambiguous facility matches are resolved.")
        st.write(
            "Future daily runs will reuse the saved website-to-CRM decisions. "
            "New ambiguous facilities will appear here automatically."
        )
        return

    account_lookup = {
        account.get("account_id"): account
        for account in crm_accounts
    }

    review_rows = load_review_rows()
    review_lookup = {
        (row.get("website_source_url", ""), row.get("crm_account_id", "")): row
        for row in review_rows
    }

    search = st.text_input(
        "Search ambiguous facilities",
        placeholder="Facility, city, state...",
        key="match_review_search",
    ).strip().lower()

    filtered = []
    for case in pending:
        website = case.get("website") or {}
        haystack = " ".join(
            str(website.get(field, ""))
            for field in ["name", "address", "city", "state", "zip"]
        ).lower()

        if not search or search in haystack:
            filtered.append(case)

    if not filtered:
        st.warning("No ambiguous facilities match that search.")
        return

    inbox_rows = []
    for case in filtered:
        website = case.get("website") or {}
        inbox_rows.append(
            {
                "Facility": website.get("name", ""),
                "City": website.get("city", ""),
                "State": website.get("state", ""),
                "Candidates": len(case.get("candidate_ids", [])),
                "Status": "Needs human match",
            }
        )

    left, right = st.columns([0.9, 1.35], gap="large")

    with left:
        st.markdown("#### Review inbox")
        st.caption(f"{len(filtered)} unresolved facility case(s)")

        event = st.dataframe(
            inbox_rows,
            hide_index=True,
            use_container_width=True,
            height=min(460, 74 + 36 * len(inbox_rows)),
            on_select="rerun",
            selection_mode="single-row",
            key="match_review_inbox",
        )

        selected_rows = selected_rows_from_event(event)

        if selected_rows:
            selected_index = selected_rows[0]
            st.session_state.match_selected_source = (
                filtered[selected_index].get("website", {}).get("source_url", "")
            )

        selected_source = st.session_state.get("match_selected_source")

        selected_case = next(
            (
                case
                for case in filtered
                if case.get("website", {}).get("source_url", "") == selected_source
            ),
            filtered[0],
        )

        st.info(
            "Tip: one row is one facility case, even when several CRM records "
            "are related to it."
        )

    with right:
        website = selected_case["website"]
        candidate_ids = selected_case.get("candidate_ids", [])
        candidates = [
            account_lookup[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in account_lookup
        ]

        st.markdown(f"## {website.get('name', 'Ambiguous facility')}")
        st.caption(
            f"{website.get('city', '')}, {website.get('state', '')} • "
            f"{len(candidates)} related CRM candidate(s)"
        )

        render_website_record(website)

        st.divider()
        st.markdown("#### Related CRM candidates")

        for account in candidates:
            source_url = website.get("source_url", "")
            review_row = review_lookup.get(
                (source_url, account.get("account_id", ""))
            )

            with st.container(border=True):
                render_candidate(account, review_row)

        st.divider()
        st.markdown("#### Grouped decision")

        primary_options = ["__NONE__"] + [
            account.get("account_id", "")
            for account in candidates
        ]

        def primary_format(option):
            if option == "__NONE__":
                return "None of these records is the current facility"
            account = account_lookup.get(option, {})
            return candidate_label(account)

        case_key = website.get("source_url", website.get("name", "case"))

        primary_id = st.radio(
            "Which CRM account should survive as the PRIMARY record?",
            primary_options,
            format_func=primary_format,
            index=None,
            key=f"primary_{case_key}",
        )

        secondary_decisions = {}
        all_secondary_selected = True

        if primary_id and primary_id != "__NONE__":
            st.write("**Classify the remaining candidate records:**")

            for account in candidates:
                candidate_id = account.get("account_id", "")
                if candidate_id == primary_id:
                    continue

                decision = st.selectbox(
                    candidate_label(account),
                    ["Select decision", "DUPLICATE", "NOT_MATCH"],
                    key=f"secondary_{case_key}_{candidate_id}",
                )

                if decision == "Select decision":
                    all_secondary_selected = False
                else:
                    secondary_decisions[candidate_id] = decision

        note = st.text_area(
            "Reviewer note (optional)",
            placeholder="Why this survivor/relationship was selected.",
            key=f"note_{case_key}",
        )

        can_save = bool(primary_id)

        if primary_id and primary_id != "__NONE__":
            can_save = can_save and all_secondary_selected

        if st.button(
            "Save grouped match decision",
            type="primary",
            disabled=not can_save,
            key=f"save_{case_key}",
            use_container_width=True,
        ):
            try:
                update_grouped_match_decision(
                    website=website,
                    candidate_ids=candidate_ids,
                    primary_id=primary_id,
                    secondary_decisions=secondary_decisions,
                    note=note,
                )

                matching_output, proposals_output = refresh_matching_and_proposals()

                st.success(
                    "Match decision saved. Matching and proposals were regenerated."
                )

                with st.expander("Refresh output"):
                    st.code(
                        matching_output + "\n\n" + proposals_output,
                        language=None,
                    )

                st.session_state.pop("match_selected_source", None)
                st.rerun()

            except Exception as error:
                st.error(str(error))

def proposal_group_key(proposal):
    """
    Group all actions that belong to the same business case.

    Website-backed proposals are grouped by the website source URL.
    CRM-only stale-account proposals are grouped by their CRM account ID.
    """
    website = proposal.get("website") or {}
    crm = proposal.get("crm_account") or {}

    source_url = website.get("source_url")
    if source_url:
        return f"website::{source_url}"

    account_id = crm.get("account_id")
    if account_id:
        return f"crm::{account_id}"

    return f"proposal::{proposal.get('proposal_id', '')}"


def group_proposals(proposals):
    groups = {}

    for proposal in proposals:
        key = proposal_group_key(proposal)

        if key not in groups:
            website = proposal.get("website") or {}
            crm = proposal.get("crm_account") or {}

            groups[key] = {
                "key": key,
                "title": (
                    website.get("name")
                    or crm.get("name")
                    or "Unnamed proposal case"
                ),
                "website": website or None,
                "proposals": [],
            }

        groups[key]["proposals"].append(proposal)

    return list(groups.values())


def proposal_action_summary(proposals):
    counts = Counter(
        proposal.get("action", "UNKNOWN")
        for proposal in proposals
    )

    parts = []
    for action, count in sorted(counts.items()):
        if count == 1:
            parts.append(action)
        else:
            parts.append(f"{count}× {action}")

    return " • ".join(parts)


def display_account_summary(account, heading="CRM record"):
    if not account:
        st.write("No existing CRM account")
        return

    st.markdown(f"**{heading}**")

    c1, c2, c3 = st.columns(3)

    with c1:
        st.write("**Name**")
        st.write(account.get("name") or "—")

        st.write("**Account ID**")
        st.code(account.get("account_id", ""), language=None)

    with c2:
        st.write("**Address**")
        address = ", ".join(
            part
            for part in [
                account.get("billing_street"),
                account.get("billing_city"),
                account.get("billing_state"),
                account.get("billing_zip"),
            ]
            if part
        )
        st.write(address or "—")

        st.write("**Parent**")
        st.write(account.get("parent_name") or "No parent")

    with c3:
        st.write("**Care type**")
        st.write(account.get("care_type") or "—")

        st.write("**Revenue / AR**")
        st.write(
            f"{money(account.get('lifetime_revenue'))} / "
            f"{money(account.get('outstanding_ar'))}"
        )


def render_field_changes(account, changes):
    if not changes:
        st.write("No field changes")
        return

    rows = []

    field_labels = {
        "name": "Name",
        "parent_id": "Parent ID",
        "billing_street": "Street",
        "billing_city": "City",
        "billing_state": "State",
        "billing_zip": "ZIP",
        "care_type": "Care type",
        "status": "Status",
        "duplicate_of_account": "Duplicate of",
        "chow_current_account": "CHOW current account",
        "note": "Note",
    }

    account_field_map = {
        "name": "name",
        "parent_id": "parent_id",
        "billing_street": "billing_street",
        "billing_city": "billing_city",
        "billing_state": "billing_state",
        "billing_zip": "billing_zip",
        "care_type": "care_type",
        "status": "status",
        "duplicate_of_account": "duplicate_of_account",
        "chow_current_account": "chow_current_account",
        "note": "note",
    }

    for field, new_value in changes.items():
        old_field = account_field_map.get(field, field)
        old_value = account.get(old_field, "") if account else ""

        rows.append(
            {
                "Field": field_labels.get(field, field),
                "Current": old_value if old_value not in (None, "") else "—",
                "Proposed": new_value if new_value not in (None, "") else "—",
            }
        )

    st.table(rows)


def render_single_proposal(proposal):
    action = proposal.get("action", "UNKNOWN")
    crm = proposal.get("crm_account") or {}
    target = proposal.get("target_account") or {}
    changes = proposal.get("changes") or {}
    create_fields = proposal.get("create_fields") or {}
    evidence = proposal.get("evidence", [])

    if action == "UPDATE_ACCOUNT":
        st.markdown("### Update surviving CRM account")
        display_account_summary(crm, "Current CRM account")
        st.markdown("**Proposed changes**")
        render_field_changes(crm, changes)

    elif action == "MARK_DUPLICATE":
        st.markdown("### Mark duplicate CRM copy")
        left, right = st.columns(2)

        with left:
            display_account_summary(crm, "Losing copy")

        with right:
            display_account_summary(target, "Surviving PRIMARY account")

        st.markdown("**What will happen to the losing copy**")
        render_field_changes(crm, changes)

    elif action == "CHOW_CREATE_NEW":
        st.markdown("### CHOW: preserve old account and create new Bellhaven account")
        st.warning(
            "Revenue history and outstanding AR are both present. "
            "The old CRM account must not be re-parented."
        )

        display_account_summary(crm, "Existing historical CRM account")

        st.markdown("**New Bellhaven account to create**")
        st.table(
            [
                {"Field": field, "Value": value}
                for field, value in create_fields.items()
            ]
        )

        st.markdown("**Only change to the old account**")
        render_field_changes(crm, changes)

    elif action == "CREATE_ACCOUNT":
        st.markdown("### Create new CRM account")
        st.write("No existing CRM account was selected for this current facility.")

        st.markdown("**New account fields**")
        st.table(
            [
                {"Field": field, "Value": value}
                for field, value in create_fields.items()
            ]
        )

    elif action == "MARK_NEEDS_REVIEW":
        st.markdown("### Mark existing CRM account as Needs Review")
        display_account_summary(crm, "CRM account")
        st.markdown("**Proposed changes**")
        render_field_changes(crm, changes)

    else:
        st.markdown(f"### {action}")
        if crm:
            display_account_summary(crm, "CRM account")
        if changes:
            render_field_changes(crm, changes)
        elif create_fields:
            st.table(
                [
                    {"Field": field, "Value": value}
                    for field, value in create_fields.items()
                ]
            )

    description = proposal.get("description")
    if description:
        st.caption(description)

    if evidence:
        with st.expander("Evidence used for this action"):
            for item in evidence:
                st.write(f"• {item}")

    st.caption(f"Proposal ID: {proposal.get('proposal_id', '')}")


def case_type(group):
    actions = {
        proposal.get("action", "UNKNOWN")
        for proposal in group["proposals"]
    }

    if "CHOW_CREATE_NEW" in actions:
        return "CHOW"
    if "MARK_DUPLICATE" in actions:
        return "DUPLICATE"
    if actions == {"CREATE_ACCOUNT"}:
        return "CREATE"
    if actions == {"MARK_NEEDS_REVIEW"}:
        return "NEEDS REVIEW"
    if actions == {"UPDATE_ACCOUNT"}:
        return "UPDATE"
    return "MIXED"


def case_priority(group):
    """
    Review-priority heuristic for the UI only.
    This is not a Clipboard business rule.
    """
    priority = "LOW"

    for proposal in group["proposals"]:
        action = proposal.get("action")
        crm = proposal.get("crm_account") or {}
        changes = proposal.get("changes") or {}

        if action == "CHOW_CREATE_NEW":
            return "HIGH"

        if action == "MARK_DUPLICATE":
            revenue = float(crm.get("lifetime_revenue") or 0)
            ar = float(crm.get("outstanding_ar") or 0)

            if revenue > 0 or ar > 0:
                return "HIGH"

            priority = "MEDIUM"

        elif action == "MARK_NEEDS_REVIEW":
            priority = "MEDIUM"

        elif action == "UPDATE_ACCOUNT" and "parent_id" in changes:
            priority = "MEDIUM"

    return priority



def compact_address(account):
    return ", ".join(
        part
        for part in [
            account.get("billing_street"),
            account.get("billing_city"),
            account.get("billing_state"),
            account.get("billing_zip"),
        ]
        if part
    ) or "—"


def summarize_changes(changes):
    if not changes:
        return "Keep as current"

    labels = {
        "name": "name",
        "parent_id": "parent",
        "billing_street": "street",
        "billing_city": "city",
        "billing_state": "state",
        "billing_zip": "ZIP",
        "care_type": "care type",
        "status": "status",
        "duplicate_of_account": "duplicate link",
        "chow_current_account": "CHOW link",
        "note": "note",
    }

    useful = [
        labels.get(field, field)
        for field in changes
        if field != "note"
    ]

    if not useful:
        return "Metadata/note update"

    return "Update " + ", ".join(useful)


def render_duplicate_case(group, crm_accounts, survivor_policy):
    """
    Universal duplicate workflow.

    Clipboard defines what happens to the losing duplicate, but does not define
    how the survivor must be selected. The global survivor policy controls
    whether the automatic suggestion is accepted by default or must be
    explicitly confirmed by a human before approval.
    """
    proposals = group["proposals"]

    duplicate_proposals = [
        proposal
        for proposal in proposals
        if proposal.get("action") == "MARK_DUPLICATE"
    ]
    update_proposals = [
        proposal
        for proposal in proposals
        if proposal.get("action") == "UPDATE_ACCOUNT"
    ]

    primary = {}
    if duplicate_proposals:
        primary = duplicate_proposals[0].get("target_account") or {}

    if not primary and update_proposals:
        primary = update_proposals[0].get("crm_account") or {}

    primary_id = primary.get("account_id", "")

    primary_update = next(
        (
            proposal
            for proposal in update_proposals
            if (proposal.get("crm_account") or {}).get("account_id") == primary_id
        ),
        None,
    )

    account_lookup = {
        account.get("account_id"): account
        for account in crm_accounts
    }

    website = group.get("website") or {}
    source_url = website.get("source_url", "")

    # Existing explicit human decisions, if any.
    all_review_rows = load_review_rows()
    existing_review_rows = [
        row
        for row in all_review_rows
        if row.get("website_source_url", "") == source_url
    ]

    saved_decisions = {
        row.get("crm_account_id", ""): row.get("decision", "").strip().upper()
        for row in existing_review_rows
        if row.get("crm_account_id")
    }

    saved_primary_ids = [
        candidate_id
        for candidate_id, decision in saved_decisions.items()
        if decision == "PRIMARY"
    ]
    human_confirmed = len(saved_primary_ids) == 1

    # Candidate IDs come from the current derived duplicate case PLUS any
    # previously reviewed candidates, including old NOT_MATCH choices.
    candidate_ids = []

    if primary_id:
        candidate_ids.append(primary_id)

    for proposal in duplicate_proposals:
        candidate_id = (proposal.get("crm_account") or {}).get("account_id", "")
        if candidate_id and candidate_id not in candidate_ids:
            candidate_ids.append(candidate_id)

    for row in existing_review_rows:
        candidate_id = row.get("crm_account_id", "")
        if candidate_id and candidate_id not in candidate_ids:
            candidate_ids.append(candidate_id)

    candidate_accounts = {
        candidate_id: account_lookup.get(candidate_id, {})
        for candidate_id in candidate_ids
    }

    related_records = []

    if primary:
        related_records.append(
            {
                "role": "PRIMARY",
                "account": primary,
                "proposal": primary_update,
            }
        )

    for proposal in duplicate_proposals:
        related_records.append(
            {
                "role": "DUPLICATE",
                "account": proposal.get("crm_account") or {},
                "proposal": proposal,
            }
        )

    st.markdown("### CRM record comparison")
    st.caption(
        f"{len(related_records)} related CRM records. "
        f"{len(proposals)} CRM action(s) are currently proposed."
    )

    main_rows = []

    for item in related_records:
        role = item["role"]
        account = item["account"]

        if role == "PRIMARY":
            if primary_update and primary_update.get("changes"):
                outcome = "Keep Active; update current Bellhaven identity"
            else:
                outcome = "Keep Active as PRIMARY"
        else:
            outcome = "Set Inactive; link to PRIMARY"

        main_rows.append(
            {
                "Role": role,
                "CRM record": account.get("name", ""),
                "Current parent": account.get("parent_name") or "No parent",
                "Current status": account.get("status") or "—",
                "After approval": outcome,
            }
        )

    st.table(main_rows)

    # ----------------------------------------------------------------
    # Duplicate resolution summary
    # ----------------------------------------------------------------
    if human_confirmed:
        selected_source = "Human override / confirmation"
        displayed_primary_id = saved_primary_ids[0]
    else:
        selected_source = "Matching automation"
        displayed_primary_id = primary_id

    displayed_primary = account_lookup.get(displayed_primary_id, primary)

    st.markdown("### Duplicate resolution")

    s1, s2 = st.columns([1.4, 1])
    with s1:
        st.write("**Current survivor (PRIMARY)**")
        st.write(displayed_primary.get("name", "—"))
    with s2:
        st.write("**Selected by**")
        st.write(selected_source)

    if survivor_policy == "automatic":
        st.caption(
            "Automatic policy: the matcher suggestion can proceed without a separate "
            "survivor-confirmation click. Reviewers can still override any case."
        )
        editor_title = "Change survivor / relationships (optional)"
    else:
        if human_confirmed:
            st.success("Survivor explicitly confirmed by a reviewer.")
            editor_title = "Change confirmed survivor / relationships"
        else:
            st.warning(
                "Manual policy: this survivor must be explicitly confirmed before "
                "the case can be approved."
            )
            editor_title = "Confirm survivor / relationships"

    # Keep the case clean: detailed controls only appear when needed.
    with st.expander(editor_title, expanded=(survivor_policy == "manual" and not human_confirmed)):
        st.markdown(
            "**PRIMARY** = surviving CRM account.  \n"
            "**DUPLICATE** = losing copy; after approval it becomes Inactive and "
            "points to the PRIMARY.  \n"
            "**NOT_MATCH** = not the same facility; exclude it from this duplicate case."
        )

        def candidate_label(account_id):
            account = account_lookup.get(account_id, {})
            parent = account.get("parent_name") or "No parent"
            address = account.get("billing_street") or "No street"
            return (
                f"{account.get('name', account_id)} | "
                f"{address} | {parent}"
            )

        suggested_primary_id = displayed_primary_id or primary_id

        if suggested_primary_id in candidate_ids:
            primary_index = candidate_ids.index(suggested_primary_id)
        else:
            primary_index = 0

        selected_primary_id = st.radio(
            "Choose the surviving PRIMARY account",
            candidate_ids,
            format_func=candidate_label,
            index=primary_index,
            key=f"universal_primary_{source_url}",
        )

        secondary_decisions = {}

        st.write("**Classify the other related records:**")

        current_duplicate_ids = {
            (proposal.get("crm_account") or {}).get("account_id", "")
            for proposal in duplicate_proposals
        }

        for candidate_id in candidate_ids:
            if candidate_id == selected_primary_id:
                continue

            existing_decision = saved_decisions.get(candidate_id)

            if existing_decision not in {"DUPLICATE", "NOT_MATCH"}:
                existing_decision = (
                    "DUPLICATE"
                    if candidate_id in current_duplicate_ids
                    else "NOT_MATCH"
                )

            choices = ["DUPLICATE", "NOT_MATCH"]
            default_index = choices.index(existing_decision)

            secondary_decisions[candidate_id] = st.selectbox(
                candidate_label(candidate_id),
                choices,
                index=default_index,
                key=f"universal_secondary_{source_url}_{candidate_id}",
            )

        existing_note = ""
        if existing_review_rows:
            existing_note = next(
                (
                    row.get("note", "")
                    for row in existing_review_rows
                    if row.get("decision", "").strip()
                ),
                "",
            )

        reviewer_note = st.text_area(
            "Reviewer note (optional)",
            value=existing_note,
            placeholder="Why this survivor / relationship was selected.",
            key=f"universal_note_{source_url}",
        )

        button_text = (
            "Confirm duplicate resolution"
            if survivor_policy == "manual" and not human_confirmed
            else "Save duplicate resolution"
        )

        if st.button(
            button_text,
            type="primary",
            use_container_width=True,
            key=f"save_universal_duplicate_{source_url}",
        ):
            try:
                update_grouped_match_decision(
                    website=website,
                    candidate_ids=candidate_ids,
                    primary_id=selected_primary_id,
                    secondary_decisions=secondary_decisions,
                    note=reviewer_note,
                    candidate_accounts=candidate_accounts,
                )

                matching_output, proposals_output = refresh_matching_and_proposals()

                st.success(
                    "Duplicate resolution saved. Matching and dependent proposals "
                    "were regenerated."
                )

                with st.expander("Refresh output"):
                    st.code(
                        matching_output + "\n\n" + proposals_output,
                        language=None,
                    )

                st.rerun()

            except Exception as error:
                st.error(str(error))

    # ----------------------------------------------------------------
    # Secondary details
    # ----------------------------------------------------------------
    with st.expander("More record details"):
        details_rows = []

        for candidate_id in candidate_ids:
            account = account_lookup.get(candidate_id, {})
            current_role = (
                "PRIMARY"
                if candidate_id == primary_id
                else (
                    "DUPLICATE"
                    if any(
                        (proposal.get("crm_account") or {}).get("account_id")
                        == candidate_id
                        for proposal in duplicate_proposals
                    )
                    else "NOT_MATCH / historical candidate"
                )
            )

            details_rows.append(
                {
                    "Current role": current_role,
                    "CRM record": account.get("name", ""),
                    "Address": compact_address(account),
                    "Care type": account.get("care_type") or "—",
                    "Revenue": money(account.get("lifetime_revenue")),
                    "Outstanding AR": money(account.get("outstanding_ar")),
                }
            )

        st.table(details_rows)

    st.markdown("### Proposed CRM outcome")
    st.caption(
        "One row represents one CRM record. If the survivor is changed, saving "
        "the resolution regenerates these outcomes."
    )

    outcome_rows = []

    for item in related_records:
        role = item["role"]
        account = item["account"]

        if role == "PRIMARY":
            proposed_parts = []

            if primary_update:
                changes = primary_update.get("changes") or {}

                if "name" in changes:
                    proposed_parts.append(f"Rename → {changes['name']}")

                if "parent_id" in changes:
                    proposed_parts.append("Parent → Bellhaven Senior Living")

                if "billing_street" in changes:
                    proposed_parts.append(
                        f"Street → {changes['billing_street']}"
                    )

                if "billing_city" in changes:
                    proposed_parts.append(
                        f"City → {changes['billing_city']}"
                    )

                if "billing_state" in changes:
                    proposed_parts.append(
                        f"State → {changes['billing_state']}"
                    )

                if "billing_zip" in changes:
                    proposed_parts.append(
                        f"ZIP → {changes['billing_zip']}"
                    )

                if "care_type" in changes:
                    proposed_parts.append(
                        f"Care type → {changes['care_type']}"
                    )

            proposed_outcome = (
                "; ".join(proposed_parts)
                if proposed_parts
                else "Keep current PRIMARY record"
            )

        else:
            proposed_outcome = (
                f"Status → Inactive; "
                f"Duplicate of → {primary.get('name', 'PRIMARY')}"
            )

        outcome_rows.append(
            {
                "Role": role,
                "CRM record": account.get("name", ""),
                "Proposed outcome": proposed_outcome,
            }
        )

    st.table(outcome_rows)

    with st.expander("Detailed field changes"):
        field_rows = []

        if primary_update:
            current = primary_update.get("crm_account") or {}
            changes = primary_update.get("changes") or {}

            for field, proposed in changes.items():
                if field == "note":
                    continue

                current_value = current.get(field, "—")

                if field == "parent_id":
                    current_value = (
                        current.get("parent_name")
                        or current.get("parent_id")
                        or "No parent"
                    )
                    proposed = "Bellhaven Senior Living"

                field_rows.append(
                    {
                        "CRM record": current.get("name", "PRIMARY"),
                        "Field": field,
                        "Current": (
                            current_value
                            if current_value not in ("", None)
                            else "—"
                        ),
                        "Proposed": (
                            proposed
                            if proposed not in ("", None)
                            else "—"
                        ),
                    }
                )

        for proposal in duplicate_proposals:
            losing = proposal.get("crm_account") or {}

            field_rows.append(
                {
                    "CRM record": losing.get("name", ""),
                    "Field": "status",
                    "Current": losing.get("status") or "—",
                    "Proposed": "Inactive",
                }
            )
            field_rows.append(
                {
                    "CRM record": losing.get("name", ""),
                    "Field": "duplicate_of_account",
                    "Current": losing.get("duplicate_of_account") or "—",
                    "Proposed": primary.get("name", "PRIMARY"),
                }
            )

        if field_rows:
            st.table(field_rows)

    evidence = []
    for proposal in proposals:
        for item in proposal.get("evidence", []):
            if item not in evidence:
                evidence.append(item)

    if evidence:
        with st.expander("Why these records are related"):
            for item in evidence:
                st.write(f"✓ {item}")

    with st.expander("Technical details"):
        technical_rows = []

        for candidate_id in candidate_ids:
            account = account_lookup.get(candidate_id, {})
            matching_proposal = next(
                (
                    proposal
                    for proposal in proposals
                    if (
                        (proposal.get("crm_account") or {}).get("account_id")
                        == candidate_id
                        or
                        (proposal.get("target_account") or {}).get("account_id")
                        == candidate_id
                    )
                ),
                None,
            )

            technical_rows.append(
                {
                    "CRM record": account.get("name", ""),
                    "Account ID": candidate_id,
                    "Proposal ID": (
                        matching_proposal.get("proposal_id", "")
                        if matching_proposal
                        else "No current CRM action"
                    ),
                }
            )

        st.table(technical_rows)

def render_proposal_review(proposals, crm_accounts):
    st.subheader("Proposal Review")
    st.caption(
        "One inbox row represents one facility/business case. Related CRM actions "
        "stay together in the detail panel."
    )

    review_settings = load_review_settings()
    saved_policy = review_settings.get("duplicate_survivor_policy", "automatic")

    with st.expander("⚙ Review settings", expanded=True):
        st.markdown("#### Duplicate survivor policy")

        st.markdown(
            """
<div style="
    border-left: 4px solid #ff4b4b;
    background: rgba(255, 75, 75, 0.08);
    padding: 0.80rem 0.95rem;
    border-radius: 0.45rem;
    margin: 0.35rem 0 1rem 0;
">
    <div style="font-weight: 700; margin-bottom: 0.30rem;">
        Why this setting exists
    </div>
    <div style="line-height: 1.5;">
        Clipboard defines what happens to the <b>losing duplicate</b>, but does
        not define which matching CRM account must survive.
        <br><br>
        <b>Example:</b> Bellhaven of Kettering matched Kettering Nursing &amp;
        Rehabilitation, Kettering Care Centre, and Kettering Senior Campus at
        the same location. The evidence supported treating them as the same
        facility, but there was no rule saying which record had to be PRIMARY.
        <br><br>
        Instead of hard-coding one arbitrary approach, this tool supports both
        <b>automatic survivor suggestion</b> and <b>explicit human selection</b>.
        The reviewer can still override the survivor before CRM approval.
    </div>
</div>
            """,
            unsafe_allow_html=True,
        )

        policy_labels = {
            "automatic": "Use automatic survivor suggestion",
            "manual": "Require explicit human survivor selection",
        }
        label_to_policy = {label: key for key, label in policy_labels.items()}

        chosen_label = st.radio(
            "How should duplicate survivors be reviewed?",
            list(label_to_policy.keys()),
            index=0 if saved_policy == "automatic" else 1,
            key="duplicate_survivor_policy_control",
        )

        survivor_policy = label_to_policy[chosen_label]

        if survivor_policy != saved_policy:
            save_review_settings(
                {"duplicate_survivor_policy": survivor_policy}
            )
            st.success("Review setting saved.")

        if survivor_policy == "automatic":
            st.caption(
                "The matcher chooses the likely survivor. No separate confirmation "
                "is required for every duplicate case, but the reviewer can override "
                "the survivor before CRM approval."
            )
        else:
            st.caption(
                "The matcher can suggest a survivor, but every duplicate case must "
                "be explicitly confirmed by a reviewer before CRM approval."
            )

    if not proposals:
        st.info(
            "No proposals are currently available. Resolve pending match cases "
            "or run the daily refresh first."
        )
        return

    groups = group_proposals(proposals)

    for group in groups:
        group["type"] = case_type(group)
        group["priority"] = case_priority(group)

    type_counts = Counter(group["type"] for group in groups)
    high_count = sum(1 for group in groups if group["priority"] == "HIGH")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Pending cases", len(groups))
    m2.metric("High priority", high_count)
    m3.metric("CHOW", type_counts.get("CHOW", 0))
    m4.metric("Duplicate cases", type_counts.get("DUPLICATE", 0))
    m5.metric("Create cases", type_counts.get("CREATE", 0))

    history = decision_counts()
    if history.get("APPLIED", 0) or history.get("REJECTED", 0):
        st.caption(
            f"Decision history: {history.get('APPLIED', 0)} applied CRM action(s), "
            f"{history.get('REJECTED', 0)} rejected action(s)."
        )

    st.caption(
        "Priority is our review-order heuristic, not a Clipboard rule. "
        "CHOW and financially sensitive duplicate cases are surfaced first."
    )

    f1, f2, f3, f4 = st.columns([2.2, 1.1, 1.1, 1.2])

    with f1:
        search = st.text_input(
            "Search",
            placeholder="Facility, city, account ID...",
            key="proposal_search",
        ).strip().lower()

    all_types = sorted({group["type"] for group in groups})

    with f2:
        selected_type = st.selectbox(
            "Type",
            ["All"] + all_types,
            key="proposal_type_filter",
        )

    with f3:
        selected_priority = st.selectbox(
            "Priority",
            ["All", "HIGH", "MEDIUM", "LOW"],
            key="proposal_priority_filter",
        )

    with f4:
        sort_choice = st.selectbox(
            "Sort",
            ["Priority first", "Facility A-Z", "Most actions"],
            key="proposal_sort",
        )

    filtered = []

    for group in groups:
        website = group.get("website") or {}
        account_text = " ".join(
            str((proposal.get("crm_account") or {}).get("account_id", ""))
            for proposal in group["proposals"]
        )
        haystack = " ".join(
            [
                group["title"],
                website.get("city", ""),
                website.get("state", ""),
                website.get("zip", ""),
                account_text,
            ]
        ).lower()

        if search and search not in haystack:
            continue

        if selected_type != "All" and group["type"] != selected_type:
            continue

        if (
            selected_priority != "All"
            and group["priority"] != selected_priority
        ):
            continue

        filtered.append(group)

    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    if sort_choice == "Priority first":
        filtered.sort(
            key=lambda group: (
                priority_rank[group["priority"]],
                group["title"].lower(),
            )
        )
    elif sort_choice == "Facility A-Z":
        filtered.sort(key=lambda group: group["title"].lower())
    elif sort_choice == "Most actions":
        filtered.sort(
            key=lambda group: (
                -len(group["proposals"]),
                group["title"].lower(),
            )
        )

    if not filtered:
        st.warning("No proposal cases match the current filters.")
        return

    inbox_rows = []
    for group in filtered:
        website = group.get("website") or {}
        inbox_rows.append(
            {
                "Facility / Case": group["title"],
                "Location": (
                    f"{website.get('city', '')}, {website.get('state', '')}"
                    if website
                    else "CRM-only"
                ),
                "Actions": len(group["proposals"]),
                "Type": group["type"],
                "Priority": group["priority"],
                "Status": "PENDING",
            }
        )

    left, right = st.columns([1.0, 1.4], gap="large")

    with left:
        st.markdown("#### Review inbox")
        st.caption(f"{len(filtered)} case(s) shown")

        event = st.dataframe(
            inbox_rows,
            hide_index=True,
            use_container_width=True,
            height=min(620, 110 + 36 * len(inbox_rows)),
            on_select="rerun",
            selection_mode="single-row",
            key="proposal_review_inbox",
        )

        selected_rows = selected_rows_from_event(event)

        if selected_rows:
            selected_index = selected_rows[0]
            st.session_state.proposal_selected_key = filtered[selected_index]["key"]

        selected_key = st.session_state.get("proposal_selected_key")

        selected_group = next(
            (
                group
                for group in filtered
                if group["key"] == selected_key
            ),
            filtered[0],
        )

        st.info(
            "Use search and filters to narrow a large queue. "
            "Selecting a row opens the entire facility case on the right."
        )

    with right:
        group = selected_group
        visible_index = filtered.index(group)

        nav1, nav2, nav3 = st.columns([1, 3, 1])

        with nav1:
            if st.button(
                "← Previous",
                disabled=visible_index == 0,
                use_container_width=True,
                key="proposal_prev_case",
            ):
                st.session_state.proposal_selected_key = (
                    filtered[visible_index - 1]["key"]
                )
                st.rerun()

        with nav2:
            st.markdown(
                f"<div style='text-align:center; padding-top:8px;'>"
                f"<strong>Case {visible_index + 1} of {len(filtered)}</strong><br>"
                f"{group['title']}"
                f"</div>",
                unsafe_allow_html=True,
            )

        with nav3:
            if st.button(
                "Next →",
                disabled=visible_index == len(filtered) - 1,
                use_container_width=True,
                key="proposal_next_case",
            ):
                st.session_state.proposal_selected_key = (
                    filtered[visible_index + 1]["key"]
                )
                st.rerun()

        p1, p2, p3 = st.columns(3)
        p1.metric("Type", group["type"])
        p2.metric("Priority", group["priority"])
        p3.metric("Actions", len(group["proposals"]))

        if group.get("website"):
            render_website_record(group["website"])
            st.divider()

        st.caption(proposal_action_summary(group["proposals"]))

        if group["type"] == "DUPLICATE":
            render_duplicate_case(group, crm_accounts, survivor_policy)
        else:
            for action_index, proposal in enumerate(
                group["proposals"],
                start=1,
            ):
                action = proposal.get("action", "UNKNOWN")

                with st.container(border=True):
                    st.markdown(
                        f"**Action {action_index}: `{action}`**"
                    )
                    render_single_proposal(proposal)

        st.divider()
        st.markdown("### Review decision")

        proposal_ids = [
            proposal.get("proposal_id", "")
            for proposal in group["proposals"]
        ]

        manual_duplicate_block = (
            group["type"] == "DUPLICATE"
            and survivor_policy == "manual"
            and not duplicate_case_human_confirmed(group)
        )

        if manual_duplicate_block:
            st.warning(
                "Approval is blocked by the current review policy. "
                "Confirm the duplicate survivor above first."
            )

        if not has_crm_token():
            st.error(
                "CRM_TOKEN is not available in `.env`. Reject is still available, "
                "but approved changes cannot be written until the token is configured."
            )

        st.caption(
            "Approve applies all actions in this business case in a safe order. "
            "Because the CRM API does not provide a multi-account transaction, "
            "each successful action is logged immediately. If a later action fails, "
            "the unfinished action remains pending for retry."
        )

        reviewed = st.checkbox(
            "I reviewed the evidence and proposed CRM changes for this case.",
            key=f"review_confirm_{group['key']}",
        )

        reject_col, approve_col = st.columns([1, 1.6])

        with reject_col:
            if st.button(
                "Reject case",
                use_container_width=True,
                key=f"reject_case_{group['key']}",
            ):
                try:
                    reject_case(
                        group["proposals"],
                        case_key=group["key"],
                        case_title=group["title"],
                    )
                    refresh_after_reject()
                    st.session_state.pop("proposal_selected_key", None)
                    st.success(
                        f"Rejected {len(proposal_ids)} CRM action(s). "
                        "No CRM data was changed."
                    )
                    st.rerun()
                except Exception as error:
                    st.error(f"Could not record rejection: {error}")

        with approve_col:
            approve_disabled = (
                not reviewed
                or manual_duplicate_block
                or not has_crm_token()
            )

            if st.button(
                "Approve & apply to CRM",
                type="primary",
                use_container_width=True,
                disabled=approve_disabled,
                key=f"approve_case_{group['key']}",
            ):
                try:
                    with st.spinner(
                        "Applying approved CRM changes and verifying fresh state..."
                    ):
                        completed = apply_case(
                            group["proposals"],
                            case_key=group["key"],
                            case_title=group["title"],
                        )

                        crm_output, matching_output, proposals_output = (
                            refresh_after_crm_write()
                        )

                    st.session_state.pop("proposal_selected_key", None)
                    st.success(
                        f"Applied {len(completed)} CRM action(s) successfully. "
                        "The CRM snapshot, matches, and proposal queue were refreshed."
                    )

                    with st.expander("Verification output"):
                        st.code(
                            crm_output
                            + "\n\n"
                            + matching_output
                            + "\n\n"
                            + proposals_output,
                            language=None,
                        )

                    st.rerun()

                except CRMWriteError as error:
                    st.error(
                        "CRM write stopped. Any earlier action that completed "
                        "successfully was recorded; the failed/unstarted action "
                        f"remains pending. Details: {error}"
                    )
                except Exception as error:
                    st.error(
                        "The approved write or post-write refresh did not finish "
                        f"cleanly: {error}"
                    )

def main():
    st.set_page_config(
        page_title="Bellhaven CRM Review",
        page_icon="🏥",
        layout="wide",
    )

    st.title("Bellhaven CRM Reconciliation")
    st.caption(
        "High-confidence matches are automated. Ambiguous identity decisions "
        "and CRM changes require human review."
    )

    matches = load_json(MATCHES_FILE)
    crm_accounts = load_json(CRM_FILE)
    proposals = load_json(PROPOSALS_FILE)

    if not MATCHES_FILE.exists() or not CRM_FILE.exists():
        st.error(
            "Required data files are missing. Run `python run_daily.py` first."
        )
        return

    pending_count = sum(
        1
        for item in matches
        if item.get("status") in {"PENDING_HUMAN_REVIEW", "REVIEW_ERROR"}
    )

    top1, top2, top3 = st.columns(3)
    top1.metric("Website facilities", len(matches))
    top2.metric("Ambiguous facilities", pending_count)
    top3.metric("Pending CRM actions", len(proposals))

    match_tab, proposal_tab = st.tabs(
        ["1. Match Review", "2. Proposal Review"]
    )

    with match_tab:
        render_match_review(matches, crm_accounts)

    with proposal_tab:
        render_proposal_review(proposals, crm_accounts)


if __name__ == "__main__":
    main()
