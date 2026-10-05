import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from pathlib import Path

BASE_URL = "https://analyst-assessment-production.up.railway.app"

INPUT_DIR = Path("input")
INPUT_DIR.mkdir(exist_ok=True)

OUTPUT_FILE = INPUT_DIR / "website_locations.json"


def get_community_links():
    links = []
    seen = set()

    for page in range(1, 10):
        url = f"{BASE_URL}/communities?page={page}"

        response = requests.get(url, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        new_links = 0

        for a in soup.find_all("a", href=True):
            href = a["href"]

            if href.startswith("/communities/"):
                full_url = urljoin(BASE_URL, href)

                if full_url not in seen:
                    seen.add(full_url)
                    links.append(full_url)
                    new_links += 1

        if new_links == 0:
            break

    return links


def scrape_community(url):
    response = requests.get(url, timeout=20)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = list(soup.stripped_strings)

    h1 = soup.find("h1")

    if not h1:
        raise ValueError(f"No facility name found on {url}")

    name = h1.get_text(strip=True)

    address_index = text.index("Address")

    street = text[address_index + 1]
    city_state_zip = text[address_index + 2]

    match = re.match(
        r"^(.*),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$",
        city_state_zip
    )

    if not match:
        raise ValueError(
            f"Could not parse address for {name}: {city_state_zip}"
        )

    city = match.group(1).strip()
    state = match.group(2).strip()
    zip_code = match.group(3).strip()

    care_index = text.index("Care Offerings")
    admin_index = text.index("Administrator")

    care_offerings = text[care_index + 1:admin_index]

    return {
        "name": name,
        "address": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "care_offerings": care_offerings,
        "source_url": url
    }


def main():
    links = get_community_links()

    print(f"Found {len(links)} community pages")

    communities = []

    for link in links:
        try:
            community = scrape_community(link)
            communities.append(community)

            print(
                f"{community['name']} - "
                f"{community['city']} {community['state']}"
            )

        except Exception as error:
            print(f"ERROR scraping {link}")
            print(error)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            communities,
            f,
            indent=2,
            ensure_ascii=False
        )

    print()
    print(
        f"Saved {len(communities)} communities "
        f"to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()