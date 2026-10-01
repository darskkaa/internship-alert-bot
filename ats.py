import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from sources import (
    ALLOWED_SEASONS, age_from_date, check_oa_lc, classify_category, parse_iso_utc, strip_tracking_params, utc_today,
)

log = logging.getLogger("internship-watch.ats")

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"
INTERN_RE = re.compile(r"\b(interns?|internships?|co-?ops?)\b", re.I)
SEASON_RE = re.compile(r"\b(Summer|Fall|Winter|Spring)\s+(20\d\d)\b", re.I)
TIMEOUT = 20
MAX_WORKERS = 16

BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}


# One pooled session reused across runs and worker threads: ~500 board
# fetches per run against three hosts would otherwise each pay a fresh
# TCP + TLS handshake.
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=MAX_WORKERS))


def _build(board: dict, title, url, location, published, remote, today: date, force_intern=False):
    title = (title or "").strip()
    if not title or not url:
        return None
    if not (force_intern or INTERN_RE.search(title)):
        return None

    # Titles that name a season are only kept for the seasons this bot is for;
    # titles that name none are kept with no season rather than a guessed one.
    named = [f"{m.group(1).title()} {m.group(2)}" for m in SEASON_RE.finditer(title)]
    wanted = [s for s in named if s in ALLOWED_SEASONS]
    if named and not wanted:
        return None
    season = ", ".join(dict.fromkeys(wanted)) or None

    posted_date, age_days, age_label = None, None, ""
    if published is not None:
        posted_date = published.date()
        age_days, age_label = age_from_date(posted_date, today)

    company = board["name"]
    return {
        "company": company,
        "role": title,
        "location": location or "N/A",
        "apply_url": strip_tracking_params(url),
        "category": classify_category(title),
        "posted_date": posted_date,
        "posted_at": published,
        "age_days": age_days,
        "age_label": age_label,
        "oa_lc_flag": check_oa_lc(f"{company} {title}"),
        "sponsorship_flag": False,
        "citizenship_flag": False,
        "source": board["ats"].title(),
        "feed": f"{board['ats']}:{board['token']}",
        "season": season,
        "remote": bool(remote),
    }


def parse_greenhouse(data: dict, board: dict, today: date) -> list:
    items = (
        _build(
            board, job.get("title"), job.get("absolute_url"), (job.get("location") or {}).get("name"),
            # first_published is the real posting time; updated_at moves on every edit.
            parse_iso_utc(job.get("first_published")), False, today,
        )
        for job in data.get("jobs", [])
    )
    return [item for item in items if item]


def parse_lever(data: list, board: dict, today: date) -> list:
    items = []
    for job in data:
        created = job.get("createdAt")
        published = datetime.fromtimestamp(created / 1000, timezone.utc) if isinstance(created, (int, float)) else None
        categories = job.get("categories") or {}
        items.append(_build(
            board, job.get("text"), job.get("hostedUrl"), categories.get("location"),
            published, job.get("workplaceType") == "remote", today,
        ))
    return [item for item in items if item]


def parse_ashby(data: dict, board: dict, today: date) -> list:
    items = []
    for job in data.get("jobs", []):
        if not job.get("isListed", True):
            continue
        items.append(_build(
            board, job.get("title"), job.get("jobUrl") or job.get("applyUrl"), job.get("location"),
            parse_iso_utc(job.get("publishedAt")), job.get("isRemote"), today,
            force_intern=job.get("employmentType") == "Intern",
        ))
    return [item for item in items if item]


PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}


def fetch_board(board: dict, today: date) -> list:
    resp = SESSION.get(BOARD_URLS[board["ats"]].format(token=board["token"]), timeout=TIMEOUT)
    resp.raise_for_status()
    return PARSERS[board["ats"]](json.loads(resp.content.decode("utf-8")), board, today)


def load_watchlist() -> list:
    if not WATCHLIST_FILE.exists():
        return []
    return [b for b in json.loads(WATCHLIST_FILE.read_text(encoding="utf-8")) if b.get("ats") in PARSERS]


def fetch_ats() -> list:
    boards = load_watchlist()
    today = utc_today()

    def run(board):
        try:
            return board, fetch_board(board, today)
        except Exception as exc:
            log.warning("Board %s/%s failed, skipping this cycle: %s", board["ats"], board["token"], type(exc).__name__)
            return board, None

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        results = list(pool.map(run, boards))
    if boards and all(items is None for _, items in results):
        raise RuntimeError("every watchlist board failed")
    # Boards fetched successfully count as "seen" even with zero intern roles,
    # so the first role a quiet board ever posts alerts instead of being
    # swallowed as that feed's silent first-sight seeding.
    fetch_ats.fetched_feeds = {f"{b['ats']}:{b['token']}" for b, items in results if items is not None}
    return [item for _, items in results if items for item in items]


fetch_ats.fetched_feeds = set()
