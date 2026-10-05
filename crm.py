import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://analyst-assessment-production.up.railway.app/api/v1"

TOKEN = os.getenv("CRM_TOKEN")

INPUT_DIR = Path("input")
INPUT_DIR.mkdir(exist_ok=True)

OUTPUT_FILE = INPUT_DIR / "crm_accounts.json"


if not TOKEN:
    raise ValueError(
        "CRM_TOKEN was not found. "
        "Make sure it exists in your .env file."
    )


HEADERS = {
    "Authorization": f"Bearer {TOKEN}"
}


def get_all_accounts():
    accounts = []

    page = 1
    page_size = 50

    while True:
        response = requests.get(
            f"{BASE_URL}/accounts",
            headers=HEADERS,
            params={
                "page": page,
                "page_size": page_size
            },
            timeout=20
        )

        response.raise_for_status()

        result = response.json()

        page_accounts = result.get("data", [])
        total = result.get("total", 0)

        accounts.extend(page_accounts)

        print(
            f"Page {page}: got {len(page_accounts)} accounts "
            f"(total expected: {total})"
        )

        if len(accounts) >= total:
            break

        if not page_accounts:
            break

        page += 1

    return accounts


def main():
    accounts = get_all_accounts()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            accounts,
            f,
            indent=2,
            ensure_ascii=False
        )

    print()
    print(
        f"Saved {len(accounts)} CRM accounts "
        f"to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()