import json
import logging
import os
import time
from datetime import datetime, timezone
from datetime import time as dtime
from pathlib import Path

import requests
from dotenv import load_dotenv

from ats import fetch_ats, load_watchlist
from sources import SOURCES as BASE_SOURCES
from sources import job_id_key, normalize_key, utc_today

load_dotenv()

SOURCES = [*BASE_SOURCES, *([fetch_ats] if load_watchlist() else [])]
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
ROLE_PING = os.getenv("DISCORD_ROLE_ID", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "600"))
FRESHNESS_WINDOW_DAYS = 7
FIRST_SIGHT_WINDOW_HOURS = 24
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


TRACKED_STATE_DEFAULTS = {"seen": [], "seen_keys": [], "source_failures": {}, "seeded_feeds": []}
# zshah predates per-feed seeding and was already alerting, so a state file
# without seeded_feeds must not treat it as a first-sight feed.
LEGACY_FEEDS = ["Zshah"]


def save_state(state: dict) -> None:
    # Skip no-op writes: stamping last_checked_utc on every poll made the
    # Actions job commit state.json every run even when nothing changed. The
    # field now means "last time state actually changed".
    if STATE_FILE.exists():
        try:
            on_disk = json.loads(STATE_FILE.read_text())
        except ValueError:
            on_disk = None
        if isinstance(on_disk, dict) and all(
            on_disk.get(k, default) == state.get(k, default) for k, default in TRACKED_STATE_DEFAULTS.items()
        ):
            return
    state["last_checked_utc"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _sort_key(item: dict) -> datetime:
    if item.get("posted_at") is not None:
        return item["posted_at"]
    if item["posted_date"] is not None:
        return datetime.combine(item["posted_date"], dtime(0, 0), tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _dedupe_keys(item: dict) -> set:
    keys = {normalize_key(item["company"], item["role"])}
    job_key = job_id_key(item["apply_url"])
    if job_key:
        keys.add(job_key)
    return keys


def _posted_within(item: dict, now: datetime, hours: int) -> bool:
    posted = _sort_key(item)
    return posted > datetime.min.replace(tzinfo=timezone.utc) and (now - posted).total_seconds() <= hours * 3600


def build_embed(item: dict, detected_at: datetime | None = None) -> dict:
    fields = [
        {"name": "📍 Location", "value": _truncate(item["location"], 1024), "inline": True},
        {"name": "🏷️ Category", "value": item["category"], "inline": True},
    ]
    if item.get("posted_at") is not None:
        # Real posting time: Discord markup renders it in each viewer's own
        # timezone and keeps the relative part ("3 hours ago") live.
        ts = int(item["posted_at"].timestamp())
        fields.append({"name": "📅 Posted", "value": f"<t:{ts}:f> (<t:{ts}:R>)", "inline": True})
    elif item["posted_date"] is not None:
        posted_value = item["posted_date"].strftime("%b %d, %Y")
        if item["age_label"]:
            posted_value += f" · {item['age_label']}"
        fields.append({"name": "📅 Posted", "value": posted_value, "inline": True})
    # Season only when the source or title actually states one - never guessed.
    if item.get("season"):
        fields.append({"name": "🗓️ Season", "value": item["season"], "inline": True})
    if item["oa_lc_flag"]:
        fields.append({"name": "⚠️ Heads up", "value": "title mentions OA/LeetCode/assessment tooling", "inline": False})
    if item["sponsorship_flag"]:
        fields.append({"name": "🛂", "value": "No sponsorship", "inline": True})
    if item["citizenship_flag"]:
        fields.append({"name": "🇺🇸", "value": "US citizenship required", "inline": True})
    # Only zshah carries a "program" field - tag it when it's not a plain
    # internship.
    if item.get("program") and item["program"] != "Internship":
        fields.append({"name": "🔁 Program", "value": item["program"], "inline": True})
    # Only zshah carries pay data (~25% of its listings have it).
    if item.get("salary"):
        fields.append({"name": "💰 Pay", "value": item["salary"], "inline": True})
    # Only zshah carries skills tags (~87% of its listings have them).
    skills = item.get("skills")
    if skills:
        shown = skills[:SKILLS_DISPLAY_CAP]
        skills_value = ", ".join(shown)
        if len(skills) > SKILLS_DISPLAY_CAP:
            skills_value += f" +{len(skills) - SKILLS_DISPLAY_CAP} more"
        fields.append({"name": "🛠️ Skills", "value": _truncate(skills_value, 1024), "inline": True})
    # zshah and the board APIs carry a remote flag; like the other per-role
    # badges above, only rendered when true - absence never implies "not
    # remote".
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
        "footer": {"text": f"via {item['source']} · detected"},
        # When the bot actually found it - the only timestamp that is always
        # real. Posted dates are often day-granular, so they never go here.
        "timestamp": (detected_at or datetime.now(timezone.utc)).isoformat(),
    }
    return embed


MAX_EMBEDS_PER_MESSAGE = 10
# Discord rejects a message whose embeds total more than 6000 characters;
# leave headroom for fields we don't count (url, color, timestamp).
MAX_EMBED_CHARS_PER_MESSAGE = 5500
FAILURE_ALERT_THRESHOLD = 3


def _embed_chars(embed: dict) -> int:
    return (
        len(embed["title"])
        + len(embed["footer"]["text"])
        + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    )


def _batch_embeds(embeds: list) -> list:
    batches, current, current_chars = [], [], 0
    for embed in embeds:
        size = _embed_chars(embed)
        if current and (len(current) >= MAX_EMBEDS_PER_MESSAGE or current_chars + size > MAX_EMBED_CHARS_PER_MESSAGE):
            batches.append(current)
            current, current_chars = [], 0
        current.append(embed)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _send(payload: dict) -> requests.Response:
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    if resp.status_code == 429:
        time.sleep(resp.json().get("retry_after", 2))
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    return resp


def post_new_listings(new_listings: list) -> None:
    mention = f"<@&{ROLE_PING}>" if ROLE_PING else None
    detected_at = datetime.now(timezone.utc)
    embeds = [build_embed(item, detected_at) for item in new_listings]
    for batch in _batch_embeds(embeds):
        payload = {"embeds": batch}
        if mention:
            payload["content"] = mention
        resp = _send(payload)
        if resp.status_code == 400:
            # One malformed embed would otherwise fail this batch on every
            # run forever (state is never saved), blocking all later alerts.
            if len(batch) == 1:
                log.error("Discord rejected embed, skipping: %s", batch[0]["title"])
                continue
            log.warning("Batch rejected with 400, retrying embeds one at a time")
            for embed in batch:
                single = {"embeds": [embed]}
                if mention:
                    single["content"] = mention
                single_resp = _send(single)
                if single_resp.status_code == 400:
                    log.error("Discord rejected embed, skipping: %s", embed["title"])
                    continue
                single_resp.raise_for_status()
                mention = None
                time.sleep(1)
        else:
            resp.raise_for_status()
            mention = None
        time.sleep(1)


def _notify(text: str) -> None:
    try:
        _send({"content": text}).raise_for_status()
    except Exception:
        log.exception("Failed to send health notice")


def run_once(state: dict) -> dict:
    listings = []
    failures = state.setdefault("source_failures", {})
    fetched_feeds = set()
    for fetch in SOURCES:
        name = getattr(fetch, "__name__", str(fetch))
        healthy = False
        try:
            source_listings = fetch()
            source_feeds = set(getattr(fetch, "fetched_feeds", ()))
            fetched_feeds |= source_feeds
            # A board source with every board fetched fine but no intern roles
            # open right now is quiet, not broken.
            healthy = len(source_listings) > 0 or bool(source_feeds)
            if not healthy:
                log.warning("Source %s returned 0 listings - format may have changed", name)
            for item in source_listings:
                if REQUIRED_LISTING_KEYS <= item.keys():
                    listings.append(item)
                else:
                    log.warning(
                        "Dropping malformed listing from %s: missing %s",
                        name,
                        REQUIRED_LISTING_KEYS - item.keys(),
                    )
        except Exception:
            log.exception("Source %s failed, continuing with other sources", name)
        if healthy:
            failures.pop(name, None)
        else:
            failures[name] = failures.get(name, 0) + 1
            if failures[name] == FAILURE_ALERT_THRESHOLD:
                _notify(f"⚠️ Source `{name}` has failed or returned nothing {FAILURE_ALERT_THRESHOLD} runs in a row - check the bot.")

    current_ids = {item["apply_url"] for item in listings}
    current_keys = {key for item in listings for key in _dedupe_keys(item)}
    seen = set(state["seen"])
    seen_keys = set(state["seen_keys"])

    feed_of = lambda item: item.get("feed", item["source"])
    current_feeds = {feed_of(item) for item in listings} | fetched_feeds
    seeded_feeds = set(state.get("seeded_feeds", LEGACY_FEEDS))

    if not seen:
        log.info("First run: seeding state with %d existing listings, no alerts sent", len(current_ids))
        state["seeded_feeds"] = sorted(seeded_feeds | current_feeds)
        state["seen"] = sorted(current_ids)
        state["seen_keys"] = sorted(current_keys)
        return state

    today = utc_today()
    new_listings = []
    posted_keys = set()
    now = datetime.now(timezone.utc)
    for item in listings:
        keys = _dedupe_keys(item)
        if item["apply_url"] in seen or keys & seen_keys or keys & posted_keys:
            continue
        # Anything older than the freshness window is marked seen (below) so
        # it's never retried, but not posted — a genuinely old item shouldn't
        # flood the channel (e.g. a source being added mid-deployment, whose
        # whole backlog is "new" to state). Dateless items (source format
        # hiccup) can't be proven old, so they're kept, same as the existing
        # best-effort-not-authoritative treatment of other inferred fields.
        if item["posted_date"] is not None and (today - item["posted_date"]).days > FRESHNESS_WINDOW_DAYS:
            continue
        # A feed seen for the first time (new source, new watchlist board) has
        # its existing backlog marked seen below without alerting, so adding
        # 500 boards doesn't dump weeks of roles into the channel. Roles posted
        # in the last day still alert: suppressing them would also record
        # their keys, silently muting the same role when Simplify/zshah list
        # it hours later.
        if feed_of(item) not in seeded_feeds and not _posted_within(item, now, FIRST_SIGHT_WINDOW_HOURS):
            continue
        new_listings.append(item)
        posted_keys |= keys
    # Oldest first: batches are posted in this order, so the most recent
    # listing ends up in the last-sent message — the one Discord shows at
    # the bottom of the channel, i.e. what's actually visible on open.
    # Dateless items sort as "oldest" so they never bump a genuinely-dated
    # fresh listing out of that last, most-visible slot; real posting times
    # break ties within a day.
    new_listings.sort(key=_sort_key)
    if new_listings:
        log.info("Posting %d new listing(s)", len(new_listings))
        post_new_listings(new_listings)
    else:
        log.info("No new listings")

    state["seen"] = sorted(seen | current_ids)
    state["seen_keys"] = sorted(seen_keys | current_keys)
    state["seeded_feeds"] = sorted(seeded_feeds | current_feeds)
    return state
