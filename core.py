import json
import logging
import os
import time
from datetime import date, datetime, timezone
from datetime import time as dtime
from pathlib import Path

import requests
from dotenv import load_dotenv

from sources import SOURCES, normalize_key

load_dotenv()

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
ROLE_PING = os.getenv("DISCORD_ROLE_ID", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))
FRESHNESS_WINDOW_DAYS = 7
REQUIRED_LISTING_KEYS = {
    "company", "role", "location", "apply_url", "category",
    "posted_date", "age_days", "age_label", "oa_lc_flag",
    "sponsorship_flag", "citizenship_flag", "source",
}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

SKILLS_DISPLAY_CAP = 6

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
        {"name": "📍 Location", "value": _truncate(item["location"], 1024), "inline": True},
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
    # Only the zshah source carries a "program" field (Simplify/vansh are
    # internship-only) - tag it when it's not a plain internship.
    if item.get("program") and item["program"] != "Internship":
        fields.append({"name": "🔁 Program", "value": item["program"], "inline": True})
    # Only zshah carries pay data (~25% of its listings have it); Simplify
    # and vansh have no salary column at all, so this is absent for them.
    if item.get("salary"):
        fields.append({"name": "💰 Pay", "value": item["salary"], "inline": True})
    # Only zshah carries skills tags (~87% of its listings have them);
    # Simplify and vansh have no skills column, so this is absent for them.
    skills = item.get("skills")
    if skills:
        shown = skills[:SKILLS_DISPLAY_CAP]
        skills_value = ", ".join(shown)
        if len(skills) > SKILLS_DISPLAY_CAP:
            skills_value += f" +{len(skills) - SKILLS_DISPLAY_CAP} more"
        fields.append({"name": "🛠️ Skills", "value": _truncate(skills_value, 1024), "inline": True})
    # Only zshah carries a remote flag (~3% of listings); like the other
    # per-role badges above, only rendered when true - absence never
    # implies "not remote".
    if item.get("remote"):
        fields.append({"name": "🌐 Remote", "value": "Remote-eligible", "inline": True})
    # Only zshah carries H-1B data (~51% of listings), and it is a
    # *company-level* historical filing count - NOT a per-role sponsorship
    # guarantee. Deliberately worded and visually distinct (different
    # emoji/phrasing, inline:False, placed last) from the sponsorship_flag/
    # citizenship_flag badges above, which ARE per-role signals.
    if item.get("h1b_approvals"):
        fields.append({
            "name": "📊 Employer H-1B history",
            "value": f"{item['h1b_approvals']:,} approvals historically (company-wide, not role-specific)",
            "inline": False,
        })

    embed = {
        "title": _truncate(f"{item['company']} — {item['role']}", 256),
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
        # Anchored at noon UTC, not midnight: posted_date has no real time-of-
        # day component, and midnight UTC rolls back to "yesterday" for every
        # negative-UTC-offset viewer (all of North/South America) once
        # Discord converts it to their local clock. Noon UTC keeps the same
        # calendar date correct across the entire realistic timezone range.
        embed["timestamp"] = datetime.combine(item["posted_date"], dtime(12, 0), tzinfo=timezone.utc).isoformat()
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
                if REQUIRED_LISTING_KEYS <= item.keys():
                    listings.append(item)
                else:
                    log.warning(
                        "Dropping malformed listing from %s: missing %s",
                        getattr(fetch, "__name__", fetch),
                        REQUIRED_LISTING_KEYS - item.keys(),
                    )
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

    today = date.today()
    new_listings = []
    posted_keys = set()
    for item in listings:
        key = normalize_key(item["company"], item["role"])
        if item["apply_url"] in seen or key in seen_keys or key in posted_keys:
            continue
        # Anything older than the freshness window is marked seen (below) so
        # it's never retried, but not posted — a genuinely old item shouldn't
        # flood the channel (e.g. a source being added mid-deployment, whose
        # whole backlog is "new" to state). Dateless items (source format
        # hiccup) can't be proven old, so they're kept, same as the existing
        # best-effort-not-authoritative treatment of other inferred fields.
        if item["posted_date"] is not None and (today - item["posted_date"]).days > FRESHNESS_WINDOW_DAYS:
            continue
        new_listings.append(item)
        posted_keys.add(key)
    # Oldest first: batches are posted in this order, so the most recent
    # listing ends up in the last-sent message — the one Discord shows at
    # the bottom of the channel, i.e. what's actually visible on open.
    # Dateless items sort as "oldest" (date.min) so they never bump a
    # genuinely-dated fresh listing out of that last, most-visible slot.
    new_listings.sort(key=lambda item: item["posted_date"] or date.min)
    if new_listings:
        log.info("Posting %d new listing(s)", len(new_listings))
        post_new_listings(new_listings)
    else:
        log.info("No new listings")

    state["seen"] = sorted(seen | current_ids)
    state["seen_keys"] = sorted(seen_keys | current_keys)
    return state
