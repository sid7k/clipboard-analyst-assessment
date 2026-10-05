import hashlib
import json
import re
from collections import Counter
from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")

CRM_FILE = INPUT_DIR / "crm_accounts.json"
MATCHES_FILE = OUTPUT_DIR / "matches.json"
PROPOSALS_FILE = OUTPUT_DIR / "proposals.json"
MATCH_REPORT_FILE = OUTPUT_DIR / "match_report.json"
DECISIONS_FILE = OUTPUT_DIR / "decisions.json"

OUTPUT_DIR.mkdir(exist_ok=True)

ADDRESS_REPLACEMENTS = {
    "avenue": "ave",
    "street": "st",
    "road": "rd",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "highway": "hwy",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northwest": "nw",
    "northeast": "ne",
    "southwest": "sw",
    "southeast": "se",
}

CARE_TYPE_MAP = {
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value):
    value = str(value or "").lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_address(value):
    return " ".join(
        ADDRESS_REPLACEMENTS.get(word, word)
        for word in normalize_text(value).split()
    )


def find_bellhaven_parent(accounts):
    for account in accounts:
        name = normalize_text(account.get("name"))
        if "bellhaven senior living" in name and "parent account" in name:
            return account
    raise ValueError("Bellhaven parent account could not be found.")


def crm_by_id(accounts):
    return {account["account_id"]: account for account in accounts}


def get_website_care_type(website):
    mapped = []
    for offering in website.get("care_offerings", []):
        crm_value = CARE_TYPE_MAP.get(offering)
        if crm_value and crm_value not in mapped:
            mapped.append(crm_value)
    if len(mapped) == 1:
        return mapped[0]
    return None


def get_changes(website, account, bellhaven_parent_id):
    changes = {}

    if normalize_text(website.get("name")) != normalize_text(account.get("name")):
        changes["name"] = website.get("name")

    if normalize_address(website.get("address")) != normalize_address(account.get("billing_street")):
        changes["billing_street"] = website.get("address")

    if normalize_text(website.get("city")) != normalize_text(account.get("billing_city")):
        changes["billing_city"] = website.get("city")

    if str(website.get("state", "")).upper() != str(account.get("billing_state", "")).upper():
        changes["billing_state"] = website.get("state")

    if str(website.get("zip", "")) != str(account.get("billing_zip", "")):
        changes["billing_zip"] = website.get("zip")

    care_type = get_website_care_type(website)
    if care_type and normalize_text(care_type) != normalize_text(account.get("care_type")):
        changes["care_type"] = care_type

    if account.get("parent_id") != bellhaven_parent_id:
        changes["parent_id"] = bellhaven_parent_id

    return changes


def build_new_account(website, bellhaven_parent_id):
    account = {
        "name": website.get("name"),
        "parent_id": bellhaven_parent_id,
        "billing_street": website.get("address"),
        "billing_city": website.get("city"),
        "billing_state": website.get("state"),
        "billing_zip": website.get("zip"),
        "status": "Active",
    }
    care_type = get_website_care_type(website)
    if care_type:
        account["care_type"] = care_type
    return account


def account_summary(account):
    fields = [
        "account_id",
        "name",
        "parent_id",
        "parent_name",
        "billing_street",
        "billing_city",
        "billing_state",
        "billing_zip",
        "care_type",
        "status",
        "lifetime_revenue",
        "outstanding_ar",
        "chow_current_account",
        "duplicate_of_account",
    ]
    return {field: account.get(field) for field in fields}


def stable_proposal_id(data):
    serialized = json.dumps(data, sort_keys=True, default=str)
    digest = hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:14]
    return f"proposal_{digest}"


def load_decided_proposal_ids():
    """
    Suppress only proposals that reached a FINAL human decision.

    Failed/partial executions must remain eligible for review/retry.
    """
    if not DECISIONS_FILE.exists():
        return set()

    try:
        data = load_json(DECISIONS_FILE)
    except Exception:
        return set()

    final_states = {"APPLIED", "REJECTED"}
    result = set()

    def consider(row):
        if not isinstance(row, dict):
            return
        proposal_id = row.get("proposal_id")
        decision = str(
            row.get("decision")
            or row.get("status")
            or ""
        ).strip().upper()

        if proposal_id and decision in final_states:
            result.add(proposal_id)

    if isinstance(data, dict) and "decisions" in data:
        for row in data["decisions"]:
            consider(row)
    elif isinstance(data, list):
        for row in data:
            consider(row)
    elif isinstance(data, dict):
        # Conservative legacy support: only suppress dict entries whose
        # stored value explicitly indicates a final state.
        for proposal_id, value in data.items():
            if isinstance(value, str) and value.strip().upper() in final_states:
                result.add(proposal_id)
            elif isinstance(value, dict):
                consider({"proposal_id": proposal_id, **value})

    return result


def add_proposal(proposals, decided_ids, proposal):
    identity = {
        "action": proposal.get("action"),
        "website_source": proposal.get("website", {}).get("source_url") if proposal.get("website") else None,
        "crm_account_id": proposal.get("crm_account", {}).get("account_id") if proposal.get("crm_account") else None,
        "target_account_id": proposal.get("target_account", {}).get("account_id") if proposal.get("target_account") else None,
        "changes": proposal.get("changes"),
        "create_fields": proposal.get("create_fields"),
    }
    proposal_id = stable_proposal_id(identity)
    proposal["proposal_id"] = proposal_id
    if proposal_id not in decided_ids:
        proposals.append(proposal)


def primary_result(proposals, decided_ids, website, account, analysis, bellhaven_parent_id):
    wrong_parent = account.get("parent_id") != bellhaven_parent_id
    revenue = float(account.get("lifetime_revenue", 0) or 0)
    outstanding_ar = float(account.get("outstanding_ar", 0) or 0)

    # Clipboard-required CHOW SOP.
    if wrong_parent and revenue > 0 and outstanding_ar > 0:
        add_proposal(proposals, decided_ids, {
            "action": "CHOW_CREATE_NEW",
            "website": website,
            "crm_account": account_summary(account),
            "target_account": None,
            "create_fields": build_new_account(website, bellhaven_parent_id),
            "changes": {"chow_current_account": "__NEW_ACCOUNT_ID__"},
            "evidence": [
                analysis.get("reason", "Matched website facility to CRM account"),
                f"Existing CRM parent: {account.get('parent_name') or 'none'}",
                f"Lifetime revenue: {revenue}",
                f"Outstanding AR: {outstanding_ar}",
                "Clipboard CHOW rule applies because revenue history and outstanding AR are both greater than zero.",
            ],
            "description": "Create a new Bellhaven account. Leave the old account unchanged except for chow_current_account pointing to the new account.",
        })
        return "CHOW_REQUIRED"

    changes = get_changes(website, account, bellhaven_parent_id)
    if not changes:
        return "CONFIDENT_MATCH"

    add_proposal(proposals, decided_ids, {
        "action": "UPDATE_ACCOUNT",
        "website": website,
        "crm_account": account_summary(account),
        "target_account": None,
        "create_fields": None,
        "changes": changes,
        "evidence": [
            analysis.get("reason", "Matched website facility to CRM account"),
            "Bellhaven website is used as the current facility evidence.",
        ],
        "description": "Update the existing CRM account after reviewer approval.",
    })
    return "NEEDS_UPDATE"


def main():
    crm_accounts = load_json(CRM_FILE)
    matches = load_json(MATCHES_FILE)
    account_lookup = crm_by_id(crm_accounts)

    bellhaven_parent = find_bellhaven_parent(crm_accounts)
    bellhaven_parent_id = bellhaven_parent["account_id"]

    decided_ids = load_decided_proposal_ids()
    proposals = []
    report = []

    protected_ids = set()

    for match in matches:
        website = match["website"]
        status = match["status"]

        if status == "MISSING_IN_CRM":
            add_proposal(proposals, decided_ids, {
                "action": "CREATE_ACCOUNT",
                "website": website,
                "crm_account": None,
                "target_account": None,
                "create_fields": build_new_account(website, bellhaven_parent_id),
                "changes": None,
                "evidence": [
                    "Facility appears on the current Bellhaven website.",
                    "No matching CRM account was found or all candidates were manually confirmed as NOT_MATCH.",
                ],
                "description": "Create a new Bellhaven CRM account after reviewer approval.",
            })
            report.append({"website_name": website["name"], "classification": "MISSING_IN_CRM"})
            continue

        if status in {"PENDING_HUMAN_REVIEW", "REVIEW_ERROR"}:
            candidate_ids = match.get("candidate_ids", [])
            protected_ids.update(candidate_ids)
            report.append({
                "website_name": website["name"],
                "classification": status,
                "candidate_count": len(candidate_ids),
            })
            continue

        if status != "MATCHED":
            continue

        primary_id = match["primary_account_id"]
        primary = account_lookup[primary_id]
        protected_ids.add(primary_id)

        classification = primary_result(
            proposals,
            decided_ids,
            website,
            primary,
            match.get("analysis", {}),
            bellhaven_parent_id,
        )

        for duplicate_id in match.get("duplicate_account_ids", []):
            duplicate = account_lookup.get(duplicate_id)
            if not duplicate:
                continue

            protected_ids.add(duplicate_id)
            add_proposal(proposals, decided_ids, {
                "action": "MARK_DUPLICATE",
                "website": website,
                "crm_account": account_summary(duplicate),
                "target_account": account_summary(primary),
                "create_fields": None,
                "changes": {
                    "status": "Inactive",
                    "duplicate_of_account": primary_id,
                    "note": f"Matched duplicate of CRM account {primary_id}; requires reviewer approval.",
                },
                "evidence": [
                    match.get("match_reason", "Matching logic identified a duplicate at the same facility location."),
                    "Duplicate proposal still requires human approval before CRM write.",
                ],
                "description": "Mark the losing CRM copy Inactive and link duplicate_of_account to the surviving account.",
            })

        report.append({
            "website_name": website["name"],
            "classification": classification,
            "crm_account_id": primary_id,
            "crm_account_name": primary.get("name"),
        })

    # Bellhaven-owned CRM accounts not related to a current website facility.
    for account in crm_accounts:
        if account.get("parent_id") != bellhaven_parent_id:
            continue
        account_id = account.get("account_id")
        if account_id in protected_ids:
            continue

        add_proposal(proposals, decided_ids, {
            "action": "MARK_NEEDS_REVIEW",
            "website": None,
            "crm_account": account_summary(account),
            "target_account": None,
            "create_fields": None,
            "changes": {
                "status": "Needs Review",
                "note": "Account is under Bellhaven in CRM but does not appear on the current Bellhaven website. Ownership/status requires human review.",
            },
            "evidence": [
                "CRM currently links this facility to Bellhaven.",
                "No matching current Bellhaven website location was found.",
            ],
            "description": "Mark Needs Review rather than automatically making an ownership or inactive-status assumption.",
        })

    with open(PROPOSALS_FILE, "w", encoding="utf-8") as f:
        json.dump(proposals, f, indent=2, ensure_ascii=False)

    with open(MATCH_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    classifications = Counter(row["classification"] for row in report)
    proposal_actions = Counter(row["action"] for row in proposals)

    print("PROPOSALS COMPLETE")
    print("==================")
    print("MATCH RESULTS")
    for name, count in classifications.items():
        print(f"{name}: {count}")

    print()
    print("PROPOSALS")
    for action, count in proposal_actions.items():
        print(f"{action}: {count}")

    print()
    print(f"Proposals: {PROPOSALS_FILE}")
    print(f"Match report: {MATCH_REPORT_FILE}")


if __name__ == "__main__":
    main()
