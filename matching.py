import csv
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

INPUT_DIR = Path("input")
OUTPUT_DIR = Path("output")

WEBSITE_FILE = INPUT_DIR / "website_locations.json"
CRM_FILE = INPUT_DIR / "crm_accounts.json"
MATCH_REVIEW_FILE = INPUT_DIR / "match_review.csv"
MATCHES_FILE = OUTPUT_DIR / "matches.json"

INPUT_DIR.mkdir(exist_ok=True)
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


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value):
    value = str(value or "").lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_address(value):
    words = normalize_text(value).split()
    return " ".join(ADDRESS_REPLACEMENTS.get(word, word) for word in words)


def similarity(a, b, normalizer=normalize_text):
    a = normalizer(a)
    b = normalizer(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_bellhaven_parent(accounts):
    for account in accounts:
        name = normalize_text(account.get("name"))
        if "bellhaven senior living" in name and "parent account" in name:
            return account
    raise ValueError("Bellhaven parent account could not be found.")


def analyze_candidate(website, account):
    website_name = website.get("name", "")
    crm_name = account.get("name", "")
    website_address = website.get("address", "")
    crm_address = account.get("billing_street", "")

    same_name = normalize_text(website_name) == normalize_text(crm_name)
    same_address = normalize_address(website_address) == normalize_address(crm_address)
    same_city = normalize_text(website.get("city")) == normalize_text(account.get("billing_city"))
    same_state = str(website.get("state", "")).upper() == str(account.get("billing_state", "")).upper()
    same_zip = str(website.get("zip", "")) == str(account.get("billing_zip", ""))

    name_similarity = similarity(website_name, crm_name)
    address_similarity = similarity(website_address, crm_address, normalize_address)

    result = {
        "same_name": same_name,
        "same_address": same_address,
        "same_city": same_city,
        "same_state": same_state,
        "same_zip": same_zip,
        "name_similarity": round(name_similarity, 3),
        "address_similarity": round(address_similarity, 3),
    }

    if same_name and same_address and same_city and same_state:
        return {**result, "classification": "CONFIDENT", "reason": "Same normalized name, street, city and state"}

    if same_address and same_city and same_state and name_similarity >= 0.65:
        return {**result, "classification": "CONFIDENT", "reason": "Same physical address with similar facility name"}

    if same_address and same_city and same_state:
        return {**result, "classification": "REVIEW", "reason": "Same physical address but major name difference"}

    if same_name and same_city and same_state and same_zip:
        return {**result, "classification": "REVIEW", "reason": "Same facility name and location but street address differs"}

    if same_city and same_state and same_zip and name_similarity >= 0.65 and address_similarity >= 0.45:
        return {**result, "classification": "REVIEW", "reason": "Similar name/address within same city, state and ZIP"}

    if same_city and same_state and same_zip and address_similarity >= 0.85:
        return {**result, "classification": "REVIEW", "reason": "Very similar street address in the same city, state and ZIP, but identity requires human confirmation"}

    return None


def get_candidates(website, accounts, bellhaven_parent_id):
    candidates = []
    for account in accounts:
        if account.get("account_id") == bellhaven_parent_id:
            continue
        analysis = analyze_candidate(website, account)
        if analysis:
            candidates.append({"account": account, "analysis": analysis})
    return candidates


def load_previous_review():
    rows = {}
    if not MATCH_REVIEW_FILE.exists():
        return rows
    with open(MATCH_REVIEW_FILE, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row.get("website_source_url", ""), row.get("crm_account_id", ""))
            rows[key] = row
    return rows


def review_decision(previous_review, website, account):
    key = (website.get("source_url", ""), account.get("account_id", ""))
    row = previous_review.get(key, {})
    return row.get("decision", "").strip().upper()


def review_row(previous_review, website, candidate):
    account = candidate["account"]
    analysis = candidate["analysis"]
    key = (website.get("source_url", ""), account.get("account_id", ""))
    old = previous_review.get(key, {})

    return {
        "website_source_url": website.get("source_url", ""),
        "website_name": website.get("name", ""),
        "website_address": website.get("address", ""),
        "website_city": website.get("city", ""),
        "website_state": website.get("state", ""),
        "website_zip": website.get("zip", ""),
        "crm_account_id": account.get("account_id", ""),
        "crm_name": account.get("name", ""),
        "crm_address": account.get("billing_street", ""),
        "crm_city": account.get("billing_city", ""),
        "crm_state": account.get("billing_state", ""),
        "crm_zip": account.get("billing_zip", ""),
        "crm_parent": account.get("parent_name", ""),
        "reason": analysis.get("reason", ""),
        "name_similarity": analysis.get("name_similarity", ""),
        "address_similarity": analysis.get("address_similarity", ""),
        "decision": old.get("decision", ""),
        "note": old.get("note", ""),
    }


def save_review_memory(previous_review, active_rows):
    """
    Persist exact human match decisions across future runs.

    The review UI uses matches.json to decide which facilities are still pending,
    so this CSV can safely retain resolved historical decisions as matching memory.
    """
    merged = dict(previous_review)

    for row in active_rows:
        key = (row.get("website_source_url", ""), row.get("crm_account_id", ""))
        merged[key] = row

    rows = list(merged.values())
    rows.sort(
        key=lambda row: (
            row.get("website_name", ""),
            row.get("crm_name", ""),
            row.get("crm_account_id", ""),
        )
    )

    with open(MATCH_REVIEW_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def has_zero_history(account):
    revenue = float(account.get("lifetime_revenue", 0) or 0)
    ar = float(account.get("outstanding_ar", 0) or 0)
    return revenue == 0 and ar == 0


def same_physical_location(website, account):
    return (
        normalize_address(website.get("address")) == normalize_address(account.get("billing_street"))
        and normalize_text(website.get("city")) == normalize_text(account.get("billing_city"))
        and str(website.get("state", "")).upper() == str(account.get("billing_state", "")).upper()
        and str(website.get("zip", "")) == str(account.get("billing_zip", ""))
    )


def raw_address_equal(website, account):
    return normalize_text(website.get("address")) == normalize_text(account.get("billing_street"))


def exact_name(website, account):
    return normalize_text(website.get("name")) == normalize_text(account.get("name"))


def choose_automatic_primary(website, candidates, bellhaven_parent_id):
    if not candidates:
        return None, [], None

    # 1) One candidate only: allow strong identity evidence.
    if len(candidates) == 1:
        candidate = candidates[0]
        account = candidate["account"]
        analysis = candidate["analysis"]

        if analysis["classification"] == "CONFIDENT":
            return candidate, [], "Only candidate and confident match"

        if exact_name(website, account) and analysis["same_city"] and analysis["same_state"] and analysis["same_zip"]:
            return candidate, [], "Only candidate with exact facility name and same city/state/ZIP"

        if same_physical_location(website, account) and account.get("parent_id") == bellhaven_parent_id:
            return candidate, [], "Only candidate at exact facility location and already under Bellhaven"

        return None, [], None

    # 2) Prefer one exact current Bellhaven record.
    exact_bellhaven = [
        c for c in candidates
        if exact_name(website, c["account"]) and c["account"].get("parent_id") == bellhaven_parent_id
    ]
    if len(exact_bellhaven) == 1:
        primary = exact_bellhaven[0]
        duplicates = [
            c for c in candidates
            if c is not primary and same_physical_location(website, c["account"]) and has_zero_history(c["account"])
        ]
        return primary, duplicates, "Unique exact-name Bellhaven record"

    # 3) If multiple exact-name Bellhaven records exist, raw website address can break the tie.
    exact_name_bellhaven = [
        c for c in candidates
        if exact_name(website, c["account"]) and c["account"].get("parent_id") == bellhaven_parent_id
    ]
    raw_exact = [c for c in exact_name_bellhaven if raw_address_equal(website, c["account"])]
    if len(raw_exact) == 1:
        primary = raw_exact[0]
        duplicates = [
            c for c in candidates
            if c is not primary and same_physical_location(website, c["account"]) and has_zero_history(c["account"])
        ]
        return primary, duplicates, "Exact Bellhaven name with exact raw website address"

    # 4) One Bellhaven-owned record at the exact physical location can be treated as current identity.
    bellhaven_same_location = [
        c for c in candidates
        if same_physical_location(website, c["account"]) and c["account"].get("parent_id") == bellhaven_parent_id
    ]
    if len(bellhaven_same_location) == 1:
        primary = bellhaven_same_location[0]
        duplicates = [
            c for c in candidates
            if c is not primary and same_physical_location(website, c["account"]) and has_zero_history(c["account"])
        ]
        return primary, duplicates, "Unique Bellhaven-owned CRM record at exact physical location"

    return None, [], None


def account_id(candidate):
    return candidate["account"]["account_id"]


def main():
    website_locations = load_json(WEBSITE_FILE)
    crm_accounts = load_json(CRM_FILE)
    bellhaven_parent = find_bellhaven_parent(crm_accounts)
    bellhaven_parent_id = bellhaven_parent["account_id"]
    previous_review = load_previous_review()

    matches = []
    active_review_rows = []

    for website in website_locations:
        candidates = get_candidates(website, crm_accounts, bellhaven_parent_id)

        explicit_primary = []
        explicit_duplicates = []
        excluded_ids = set()

        for candidate in candidates:
            decision = review_decision(previous_review, website, candidate["account"])
            if decision == "PRIMARY":
                explicit_primary.append(candidate)
            elif decision == "DUPLICATE":
                explicit_duplicates.append(candidate)
            elif decision == "NOT_MATCH":
                excluded_ids.add(account_id(candidate))

        usable = [c for c in candidates if account_id(c) not in excluded_ids]

        if len(explicit_primary) > 1:
            for candidate in usable:
                active_review_rows.append(review_row(previous_review, website, candidate))
            matches.append({
                "website": website,
                "status": "REVIEW_ERROR",
                "reason": "More than one CRM account is marked PRIMARY",
                "candidate_ids": [account_id(c) for c in usable],
            })
            continue

        if len(explicit_primary) == 1:
            primary = explicit_primary[0]
            duplicate_ids = [account_id(c) for c in explicit_duplicates if account_id(c) != account_id(primary)]
            matches.append({
                "website": website,
                "status": "MATCHED",
                "primary_account_id": account_id(primary),
                "duplicate_account_ids": duplicate_ids,
                "match_reason": "Human-reviewed PRIMARY decision",
                "analysis": primary["analysis"],
            })
            continue

        if not usable:
            matches.append({
                "website": website,
                "status": "MISSING_IN_CRM",
                "candidate_ids": [],
            })
            continue

        primary, auto_duplicates, auto_reason = choose_automatic_primary(
            website,
            usable,
            bellhaven_parent_id,
        )

        if primary:
            duplicate_ids = {account_id(c) for c in auto_duplicates}
            duplicate_ids.update(account_id(c) for c in explicit_duplicates)
            duplicate_ids.discard(account_id(primary))

            matches.append({
                "website": website,
                "status": "MATCHED",
                "primary_account_id": account_id(primary),
                "duplicate_account_ids": sorted(duplicate_ids),
                "match_reason": auto_reason,
                "analysis": primary["analysis"],
            })
            continue

        for candidate in usable:
            active_review_rows.append(review_row(previous_review, website, candidate))

        matches.append({
            "website": website,
            "status": "PENDING_HUMAN_REVIEW",
            "candidate_ids": [account_id(c) for c in usable],
        })

    save_review_memory(previous_review, active_review_rows)

    with open(MATCHES_FILE, "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2, ensure_ascii=False)

    pending_facilities = sum(1 for row in matches if row["status"] == "PENDING_HUMAN_REVIEW")
    missing = sum(1 for row in matches if row["status"] == "MISSING_IN_CRM")
    matched = sum(1 for row in matches if row["status"] == "MATCHED")

    print("MATCHING COMPLETE")
    print("=================")
    print(f"Website facilities: {len(website_locations)}")
    print(f"Matched facilities: {matched}")
    print(f"Missing in CRM: {missing}")
    print(f"Pending human-review facilities: {pending_facilities}")
    print(f"Human match decisions still needed: {len(active_review_rows)}")
    print(f"Review file: {MATCH_REVIEW_FILE}")
    print(f"Matches: {MATCHES_FILE}")


if __name__ == "__main__":
    main()
