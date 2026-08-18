import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from sources import SOURCES, normalize_key

load_dotenv()

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
ROLE_PING = os.getenv("DISCORD_ROLE_ID", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))
REQUIRED_LISTING_KEYS = {"apply_url", "company", "role", "posted_date"}

STATE_FILE = Path(__file__).parent / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("internship-watch")


def load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if isinstance(data, list):  # migrate from the old list-only format
            return {"seen": data, "seen_keys": [], "last_checked_utc": None}
        data.setdefault("seen_keys", [])
        return data
    return {"seen": [], "seen_keys": [], "last_checked_utc": None}


def save_state(state: dict) -> None:
    state["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def build_embed(item: dict) -> dict:
    fields = [
        {"name": "📍 Location", "value": item["location"], "inline": True},
        {"name": "🏷️ Category", "value": item["category"], "inline": True},
    ]
    if item["posted_date"] is not None:
        posted_value = item["posted_date"].strftime("%b %d, %Y")
        if item["age_label"]:
            posted_value += f" · {item['age_label']}"
        fields.append({"name": "📅 Posted", "value": posted_value, "inline": True})
    if item["oa_lc_flag"]:
        fields.append({"name": "⚠️ Heads up", "value": "title mentions OA/LeetCode/assessment tooling", "inline": False})
    if item["sponsorship_flag"]:
        fields.append({"name": "🛂", "value": "No sponsorship", "inline": True})
    if item["citizenship_flag"]:
        fields.append({"name": "🇺🇸", "value": "US citizenship required", "inline": True})

    embed = {
        "title": f"{item['company']} — {item['role']}",
        "url": item["apply_url"],
        "color": 0x2ecc71,
        "fields": fields,
        "footer": {"text": f"via {item['source']}"},
    }
    if item["posted_date"] is not None:
        # Discord renders this as its own native localized timestamp in the
        # embed footer, separate from (and in addition to) the human-readable
        # "📅 Posted" field above — the field is scannable at a glance, the
        # native timestamp is accurate to the viewer's local timezone/clock.
        embed["timestamp"] = item["posted_date"].isoformat()
    return embed


def post_new_listings(new_listings: list) -> None:
    mention = f"<@&{ROLE_PING}>" if ROLE_PING else None
    for i in range(0, len(new_listings), 10):
        batch = new_listings[i:i + 10]
        embeds = [build_embed(item) for item in batch]
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
    listings = []
    for fetch in SOURCES:
        try:
            source_listings = fetch()
            for item in source_listings:
                if not REQUIRED_LISTING_KEYS <= item.keys():
                    raise ValueError(f"listing missing required keys: {REQUIRED_LISTING_KEYS - item.keys()}")
            listings.extend(source_listings)
        except Exception:
            log.exception("Source %s failed, continuing with other sources", getattr(fetch, "__name__", fetch))

    current_ids = {item["apply_url"] for item in listings}
    current_keys = {normalize_key(item["company"], item["role"]) for item in listings}
    seen = set(state["seen"])
    seen_keys = set(state["seen_keys"])

    if not seen:
        log.info("First run: seeding state with %d existing listings, no alerts sent", len(current_ids))
        state["seen"] = sorted(current_ids)
        state["seen_keys"] = sorted(current_keys)
        return state

    new_listings = []
    posted_keys = set()
    for item in listings:
        key = normalize_key(item["company"], item["role"])
        if item["apply_url"] in seen or key in seen_keys or key in posted_keys:
            continue
        new_listings.append(item)
        posted_keys.add(key)
    # Most-recently-posted first; listings with no inferred date (shouldn't
    # normally happen, but a source format hiccup could leave one dateless)
    # sort last rather than crashing the comparison.
    new_listings.sort(key=lambda item: item["posted_date"] or date.min, reverse=True)
    if new_listings:
        log.info("Posting %d new listing(s)", len(new_listings))
        post_new_listings(new_listings)
    else:
        log.info("No new listings")

    state["seen"] = sorted(seen | current_ids)
    state["seen_keys"] = sorted(seen_keys | current_keys)
    return state
