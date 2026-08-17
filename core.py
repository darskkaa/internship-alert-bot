import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

GITHUB_REPO = os.getenv("GITHUB_REPO", "SimplifyJobs/Summer2027-Internships")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "dev")
README_PATH = os.getenv("README_PATH", "README.md")
RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{README_PATH}"

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
ROLE_PING = os.getenv("DISCORD_ROLE_ID", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))

STATE_FILE = Path(__file__).parent / "state.json"

# Best-effort only: these README tables have no interview-process field at all,
# so this can only match if a role title/company literally names the tool
# (rare). It is not a real "no OA/no LeetCode" filter, just a free hint.
OA_LC_KEYWORDS = ("leetcode", "online assessment", "hackerrank", "codesignal", "coderpad")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("internship-watch")


def check_oa_lc(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in OA_LC_KEYWORDS)


def load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if isinstance(data, list):  # migrate from the old list-only format
            return {"seen": data, "last_checked_utc": None}
        return data
    return {"seen": [], "last_checked_utc": None}


def save_state(state: dict) -> None:
    state["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def fetch_readme() -> str:
    resp = requests.get(RAW_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_listings(markdown_text: str) -> list:
    soup = BeautifulSoup(markdown_text, "html.parser")
    listings = []
    last_company = None

    for table in soup.find_all("table"):
        header_row = table.find("tr")
        if not header_row or "Company" not in header_row.get_text():
            continue
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue
            company_cell, role_cell, location_cell, apply_cell = cells[0], cells[1], cells[2], cells[3]

            company_text = company_cell.get_text(strip=True)
            if company_text in ("↳", ""):
                company = last_company
            else:
                company = re.sub(r"^[^\w]+", "", company_text).strip()
                last_company = company

            if not company:
                continue

            role = role_cell.get_text(strip=True)
            location = ", ".join(location_cell.stripped_strings) or "N/A"

            apply_link = apply_cell.find("a")
            apply_url = apply_link["href"] if apply_link and apply_link.has_attr("href") else None
            if not apply_url:
                continue

            listings.append({
                "company": company,
                "role": role,
                "location": location,
                "apply_url": apply_url,
                "oa_lc_flag": check_oa_lc(f"{company} {role}"),
            })
    return listings


def post_new_listings(new_listings: list) -> None:
    mention = f"<@&{ROLE_PING}>" if ROLE_PING else None
    for i in range(0, len(new_listings), 10):
        batch = new_listings[i:i + 10]
        embeds = []
        for item in batch:
            desc = f"\U0001F4CD {item['location']}"
            if item["oa_lc_flag"]:
                desc += "\n⚠️ title mentions OA/LeetCode/assessment tooling"
            embeds.append({
                "title": f"{item['company']} — {item['role']}",
                "url": item["apply_url"],
                "description": desc,
                "color": 0x2ecc71,
            })
        payload = {"embeds": embeds}
        if mention and i == 0:
            payload["content"] = mention

        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 2)
            time.sleep(retry_after)
            resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
        time.sleep(1)


def run_once(state: dict) -> dict:
    listings = parse_listings(fetch_readme())
    current_ids = {item["apply_url"] for item in listings}
    seen = set(state["seen"])

    if not seen:
        log.info("First run: seeding state with %d existing listings, no alerts sent", len(current_ids))
        state["seen"] = sorted(current_ids)
        return state

    new_listings = [item for item in listings if item["apply_url"] not in seen]
    if new_listings:
        log.info("Posting %d new listing(s)", len(new_listings))
        post_new_listings(new_listings)
    else:
        log.info("No new listings")

    state["seen"] = sorted(seen | current_ids)
    return state
