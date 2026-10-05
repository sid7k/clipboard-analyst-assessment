import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STEPS = [
    ("1/4 Scrape Bellhaven website", "scraper.py"),
    ("2/4 Fetch CRM accounts", "crm.py"),
    ("3/4 Match website facilities to CRM", "matching.py"),
    ("4/4 Generate CRM change proposals", "proposals.py"),
]


def run_step(label, script):
    print()
    print("=" * 60)
    print(label)
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=ROOT,
    )

    if result.returncode != 0:
        print()
        print(f"FAILED: {script}")
        sys.exit(result.returncode)


def main():
    for label, script in STEPS:
        run_step(label, script)

    print()
    print("=" * 60)
    print("DAILY REFRESH COMPLETE")
    print("=" * 60)
    print("Next step: review matcher results and unresolved identity cases.")


if __name__ == "__main__":
    main()
