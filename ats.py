import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from sources import (
    ALLOWED_SEASONS, age_from_date, check_oa_lc, classify_category, parse_iso_utc, strip_tracking_params, utc_today,
)

log = logging.getLogger("internship-watch.ats")

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"
INTERN_RE = re.compile(r"\b(interns?|internships?|co-?ops?)\b", re.I)
# Company boards list every internship (accounting, HR, marketing...), unlike
# the curated tech feeds - only keep roles whose title reads as technical.
TECH_RE = re.compile(
    r"\b(engineer\w*|developer\w*|software|swe|programm\w*|coding|data|machine learning|ml|ai|artificial intelligence"
    r"|research\w*|scien\w*|quant\w*|hardware|firmware|electrical|electronic\w*|comput\w*|cyber\w*|security|infosec"
    r"|information technology|cloud|devops|sre|network\w*|systems?|robotic\w*|embedded|product (manag\w*|analyst|design\w*|owner)|technical"
    r"|technology|analytics|infrastructure|platform|mobile|web|ios|android|automation|simulation|silicon|asic|fpga"
    r"|semiconductor|algorithm\w*|blockchain|database|backend|frontend|full[ -]?stack|ux|ui|design verification"
    r"|trading|trader|fixed income|portfolio|risk|derivatives?|volatility|equit(y|ies)|investment research"
    r"|physical design|performance tools|verification|validation|test)\b",
    re.I,
)
IT_RE = re.compile(r"\bIT\b")
# Boards like Bosch or RBC post worldwide; the curated feeds are North
# America-focused, so drop roles whose location names only places outside it.
# Unknown or multi-location strings are kept rather than guessed away.
NORTH_AMERICA_RE = re.compile(
    r"\b(united states|usa|u\.s\.|us-|remote|canada|ontario|quebec|british columbia|alberta"
    r"|, ?(A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY]|ON|BC|QC|AB|MB|NS))\b",
    re.I,
)
OUTSIDE_RE = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|chennai|mumbai|delhi|gurgaon|gurugram|noida|china|shanghai|beijing"
    r"|shenzhen|suzhou|hong kong|taiwan|taipei|japan|tokyo|korea|seoul|singapore|malaysia|kuala lumpur|vietnam|ho chi minh"
    r"|hanoi|philippines|manila|thailand|bangkok|indonesia|jakarta|australia|sydney|melbourne|new zealand|germany|berlin"
    r"|munich|münchen|stuttgart|frankfurt|hamburg|france|paris|united kingdom|uk|england|london|manchester|ireland|dublin"
    r"|netherlands|amsterdam|belgium|brussels|spain|madrid|barcelona|portugal|lisbon|porto|italy|milan|rome|switzerland"
    r"|zurich|austria|vienna|poland|warsaw|krakow|czech|prague|hungary|budapest|romania|bucharest|sweden|stockholm|norway"
    r"|oslo|denmark|copenhagen|finland|helsinki|israel|tel aviv|turkey|istanbul|uae|dubai|saudi|egypt|cairo|south africa"
    r"|nigeria|kenya|brazil|são paulo|sao paulo|mexico|guadalajara|argentina|buenos aires|chile|colombia|bogota|costa rica"
    r"|serbia|belgrade|ukraine|greece|slovakia|bulgaria|croatia|lithuania|latvia|estonia)\b",
    re.I,
)
SEASON_RE = re.compile(r"\b(Summer|Fall|Winter|Spring)\s+(20\d\d)\b", re.I)
TIMEOUT = 15
MAX_WORKERS = 32

BOARD_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings?q=intern&limit=100",
}
# Workday and Oracle boards are addressed by host + site, stored in the
# watchlist token as "host|site".
WORKDAY_PAGE_SIZE = 20
WORKDAY_MAX_PAGES = 8
WORKDAY_POSTED_RE = re.compile(r"Posted (Today|Yesterday|(\d+)\+? Days Ago)", re.I)


# One pooled session reused across runs and worker threads: ~500 board
# fetches per run against three hosts would otherwise each pay a fresh
# TCP + TLS handshake.
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=MAX_WORKERS))


def _build(board: dict, title, url, location, published, remote, today: date, force_intern=False, posted_on=None):
    title = (title or "").strip()
    if not title or not url:
        return None
    if not (force_intern or INTERN_RE.search(title)):
        return None
    if not (TECH_RE.search(title) or IT_RE.search(title)):
        return None
    if location and OUTSIDE_RE.search(location) and not NORTH_AMERICA_RE.search(location):
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
    elif posted_on is not None:
        posted_date = posted_on
    if posted_date is not None:
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


def parse_smartrecruiters(data: dict, board: dict, today: date) -> list:
    items = []
    for job in data.get("content", []):
        location = job.get("location") or {}
        items.append(_build(
            board, job.get("name"), f"https://jobs.smartrecruiters.com/{board['token']}/{job.get('id')}",
            location.get("fullLocation"), parse_iso_utc(job.get("releasedDate")), location.get("remote"), today,
        ))
    return [item for item in items if item]


def workday_posted_on(text, today: date):
    # Workday only exposes a relative day count ("Posted 3 Days Ago"), never a
    # time; "30+ Days Ago" is clamped past the freshness window on purpose.
    match = WORKDAY_POSTED_RE.search(text or "")
    if not match:
        return None
    word = match.group(1).lower()
    if word == "today":
        return today
    if word == "yesterday":
        return today - timedelta(days=1)
    days = int(match.group(2))
    return today - timedelta(days=days + 1 if "+" in match.group(0) else days)


def parse_workday(postings: list, board: dict, today: date) -> list:
    host, site = board["token"].split("|")
    items = []
    for job in postings:
        path = job.get("externalPath")
        items.append(_build(
            board, job.get("title"), f"https://{host}/{site}{path}" if path else None, job.get("locationsText"),
            None, False, today, posted_on=workday_posted_on(job.get("postedOn"), today),
        ))
    return [item for item in items if item]


def fetch_workday(board: dict, today: date) -> list:
    host, site = board["token"].split("|")
    url = f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs"
    items = []
    # Workday's "intern" search isn't date-ordered on every tenant: some rank
    # by relevance (intern titles first, spread over several pages), others
    # return every job newest-first with interns scattered. Paging until a
    # page holds no intern titles covers both without walking the whole site.
    for page in range(WORKDAY_MAX_PAGES):
        body = {"appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE, "offset": page * WORKDAY_PAGE_SIZE, "searchText": "intern"}
        resp = SESSION.post(url, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        postings = json.loads(resp.content.decode("utf-8")).get("jobPostings") or []
        items += parse_workday(postings, board, today)
        if len(postings) < WORKDAY_PAGE_SIZE or not any(INTERN_RE.search(j.get("title") or "") for j in postings):
            break
    return items


def parse_oracle(data: dict, board: dict, today: date) -> list:
    host, site = board["token"].split("|")
    reqs = ((data.get("items") or [{}])[0]).get("requisitionList") or []
    items = []
    for job in reqs:
        try:
            posted_on = date.fromisoformat(job.get("PostedDate") or "")
        except ValueError:
            posted_on = None
        items.append(_build(
            board, job.get("Title"), f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job.get('Id')}",
            job.get("PrimaryLocation"), None, False, today, posted_on=posted_on,
        ))
    return [item for item in items if item]


def fetch_oracle(board: dict, today: date) -> list:
    host, site = board["token"].split("|")
    url = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
        f"&expand=requisitionList&finder=findReqs;siteNumber={site},keyword=intern,limit=50,sortBy=POSTING_DATES_DESC"
    )
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_oracle(json.loads(resp.content.decode("utf-8")), board, today)


PARSERS = {
    "greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby,
    "smartrecruiters": parse_smartrecruiters,
}
CUSTOM_FETCHERS = {"workday": fetch_workday, "oracle": fetch_oracle}


def fetch_board(board: dict, today: date) -> list:
    if board["ats"] in CUSTOM_FETCHERS:
        return CUSTOM_FETCHERS[board["ats"]](board, today)
    resp = SESSION.get(BOARD_URLS[board["ats"]].format(token=board["token"]), timeout=TIMEOUT)
    resp.raise_for_status()
    return PARSERS[board["ats"]](json.loads(resp.content.decode("utf-8")), board, today)


def load_watchlist() -> list:
    if not WATCHLIST_FILE.exists():
        return []
    return [b for b in json.loads(WATCHLIST_FILE.read_text(encoding="utf-8")) if b.get("ats") in PARSERS or b.get("ats") in CUSTOM_FETCHERS]


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
