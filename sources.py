import json
import logging
import os
import re
from datetime import date, datetime, timezone
from datetime import time as dtime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv

log = logging.getLogger("internship-watch.sources")


def utc_today() -> date:
    # Never bare date.today() - that reads the ambient system/OS timezone,
    # which drifts up to a full day from UTC depending on what machine runs
    # this (confirmed: 176/228 live listings got a different age_label under
    # local EDT "today" vs UTC "today" when checked at 11:30pm EDT, i.e.
    # already the next UTC day). posted_date for zshah is always computed in
    # UTC, so "today" must be too, or age/freshness math silently disagrees
    # with itself depending on what timezone the runner happens to be in.
    return datetime.now(timezone.utc).date()

# sources.py is imported by core.py before core.py calls load_dotenv(), so
# a .env override of GITHUB_REPO/GITHUB_BRANCH below would never take effect
# unless this module loads it too.
load_dotenv()


CATEGORY_KEYWORDS = [
    ("Quantitative Finance", ("quant", "trading", "trader")),
    ("Data Science", ("data scien", "machine learning", "ai engineer", "data analy", "data engineer", " ml ")),
    ("Product Management", ("product manager", "product management", "product specialist")),
    ("Hardware Engineering", ("hardware", "firmware", "asic", "silicon", "electrical")),
    ("Software Engineering", ("software engineer", "swe", "backend", "frontend", "full stack", "web developer")),
]


def classify_category(text: str) -> str:
    t = f" {text.lower()} "
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in t for kw in keywords):
            return category
    return "Other"


def age_from_date(posted: date, today: date):
    days = max((today - posted).days, 0)
    if days == 0:
        return 0, "today"
    if days < 30:
        return days, f"{days}d ago"
    months = round(days / 30)
    return days, f"~{months}mo ago"


def parse_iso_utc(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


GREENHOUSE_JOB_PATH_RE = re.compile(r"/jobs/(\d+)")


def job_id_key(url: str):
    # The same ATS posting reached via different feeds (Simplify, zshah, the
    # board API) often carries a different company spelling and a different
    # URL shape (careers-site ?gh_jid= vs job-boards.greenhouse.io), so
    # company+role text alone misses it. The ATS's own job id doesn't vary.
    parts = urlsplit(url)
    host = parts.netloc.lower()
    query = dict(parse_qsl(parts.query))
    if query.get("gh_jid", "").isdigit():
        return f"job:greenhouse:{query['gh_jid']}"
    if host.endswith("greenhouse.io"):
        match = GREENHOUSE_JOB_PATH_RE.search(parts.path)
        return f"job:greenhouse:{match.group(1)}" if match else None
    segments = [s for s in parts.path.split("/") if s]
    if host in ("jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com") and len(segments) >= 2:
        return f"job:{'lever' if 'lever' in host else 'ashby'}:{segments[1].lower()}"
    return None


def normalize_key(company: str, role: str) -> str:
    text = f"{company} {role}".lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Best-effort only: the feeds have no interview-process field at all, so this
# can only match if a role title/company literally names the tool (rare). It
# is not a real "no OA/no LeetCode" filter, just a free hint.
OA_LC_KEYWORDS = ("leetcode", "online assessment", "hackerrank", "codesignal", "coderpad")


def check_oa_lc(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in OA_LC_KEYWORDS)


# Routes roles to the cybersecurity channel. Whole-word matches only, so
# "securities" (finance) and "social"/"soccer" never hit; the exclusions
# catch the common non-security uses of "security" in intern titles.
CYBER_RE = re.compile(
    r"\b(cyber\w*|security|infosec|soc|siem|threat\w*|vulnerabilit\w*|pen(etration)?[ -]?test\w*"
    r"|red[ -]team\w*|blue[ -]team\w*|purple[ -]team\w*|appsec|prodsec|devsecops|secops|offensive|defensive"
    r"|incident response|digital forensic\w*|malware|reverse engineer\w*|exploit\w*|cryptograph\w*"
    r"|grc|identity and access|iam|zero trust|privacy engineer\w*)\b",
    re.I,
)
CYBER_EXCLUDE_RE = re.compile(r"\b(national security|social security|security clearance|homeland security policy)\b", re.I)


def is_cyber_role(title: str) -> bool:
    return bool(CYBER_RE.search(title)) and not CYBER_EXCLUDE_RE.search(title)


# Analytics-only query params known to be safe to drop — never remove a
# param we haven't confirmed is tracking-only, since some sources embed
# functionally required IDs (job/requisition IDs, etc.) in the query string.
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"ref", "referrer", "fbclid", "gclid", "mc_cid", "mc_eid"}


def strip_tracking_params(url: str) -> str:
    parts = urlsplit(url)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in TRACKING_PARAMS and not any(k.startswith(p) for p in TRACKING_PARAM_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


GITHUB_REPO = os.getenv("GITHUB_REPO", "SimplifyJobs/Summer2027-Internships")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "dev")
SIMPLIFY_LISTINGS_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/.github/scripts/listings.json"
ZSHAH_JOBS_URL = "https://raw.githubusercontent.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/docs/api/jobs.json"

# The Simplify feed carries every term the repo has ever tracked (Winter 2026,
# Spring 2027, ...); only alert on the seasons this bot is for. Roles whose
# only term is "N/A" have no stated season, so they are left out rather than
# guessed at.
ALLOWED_SEASONS = {"Summer 2027", "Fall 2026"}

SIMPLIFY_CATEGORIES = {
    "AI/ML/Data": "Data Science",
    "Data Science, AI & Machine Learning": "Data Science",
    "Software": "Software Engineering",
    "Software Engineering": "Software Engineering",
    "Hardware": "Hardware Engineering",
    "Hardware Engineering": "Hardware Engineering",
    "Product": "Product Management",
    "Product Management": "Product Management",
    "Quant": "Quantitative Finance",
}


def parse_simplify(data: list, today: date) -> list:
    listings = []
    for entry in data:
        # Closed/hidden roles stay in the feed forever (~74% of entries) and
        # must never alert.
        if not entry.get("active") or not entry.get("is_visible"):
            continue
        terms = [t for t in entry.get("terms") or [] if t in ALLOWED_SEASONS]
        if not terms:
            continue

        company = (entry.get("company_name") or "").strip()
        role = (entry.get("title") or "").strip()
        apply_url = entry.get("url")
        if not company or not role or not apply_url:
            continue
        apply_url = strip_tracking_params(apply_url)

        posted_date, posted_dt, age_days, age_label = None, None, None, ""
        stamp = entry.get("date_posted")
        if isinstance(stamp, (int, float)) and stamp > 0:
            posted_dt = datetime.fromtimestamp(stamp, timezone.utc)
            posted_date = posted_dt.date()
            age_days, age_label = age_from_date(posted_date, today)

        sponsorship = entry.get("sponsorship")
        listings.append({
            "company": company,
            "role": role,
            "location": ", ".join(entry.get("locations") or []) or "N/A",
            "apply_url": apply_url,
            "category": SIMPLIFY_CATEGORIES.get(entry.get("category")) or classify_category(role),
            "posted_date": posted_date,
            "posted_at": posted_dt,
            "age_days": age_days,
            "age_label": age_label,
            "oa_lc_flag": check_oa_lc(f"{company} {role}"),
            "sponsorship_flag": sponsorship == "Does Not Offer Sponsorship",
            "citizenship_flag": sponsorship == "U.S. Citizenship is Required",
            "source": "Simplify",
            "feed": "Simplify-json",
            "season": ", ".join(terms),
        })
    return listings


# This source scrapes raw ATS postings (Greenhouse/Workday/etc.) rather than
# being human-curated like Simplify, so it also carries non-internship
# program types we're not set up to track - only pull these three.
ZSHAH_ALLOWED_PROGRAMS = {"Internship", "Co-op", "Internship / Co-op"}


def parse_zshah(data: dict, today: date) -> list:
    listings = []
    for job in data.get("jobs", []):
        program = job.get("program")
        if program not in ZSHAH_ALLOWED_PROGRAMS:
            continue

        company = (job.get("company") or "").strip()
        role = (job.get("title") or "").strip()
        if not company or not role:
            continue

        apply_url = job.get("url")
        if not apply_url:
            continue
        apply_url = strip_tracking_params(apply_url)

        location = job.get("location") or "N/A"
        season = job.get("season")

        posted_date, age_days, age_label, posted_dt = None, None, "", None
        parsed = parse_iso_utc(job.get("posted_at"))
        if parsed is not None:
            posted_date = parsed.date()
            age_days, age_label = age_from_date(posted_date, today)
            # The feed labels each timestamp "exact" or "date_only"; trust
            # that over guessing. Without the label, fall back to treating
            # exactly 00:00:00 UTC as the date-only placeholder.
            source_kind = job.get("posted_at_source")
            has_real_time = source_kind == "exact" if source_kind else parsed.time() != dtime(0, 0)
            if has_real_time:
                posted_dt = parsed

        # Real sponsorship signal from ATS data (vs. the other sources' best-
        # effort keyword/emoji flags) - "offers"/"unknown" map to no flag,
        # same "don't assert what you don't know" stance used elsewhere.
        sponsorship = job.get("sponsorship")

        listings.append({
            "company": company,
            "role": role,
            "location": location,
            "apply_url": apply_url,
            "category": classify_category(role),
            "posted_date": posted_date,
            "posted_at": posted_dt,
            "age_days": age_days,
            "age_label": age_label,
            "oa_lc_flag": check_oa_lc(f"{company} {role}"),
            "sponsorship_flag": sponsorship == "no-sponsorship",
            "citizenship_flag": sponsorship == "citizens-only",
            "source": "Zshah",
            # zshah's own category tagging catches security roles whose titles
            # use none of the keywords (e.g. "Detection Engineering Intern").
            "cyber": job.get("category") == "Security",
            "program": program,
            "season": season if season and season != "Not stated" else None,
            "salary": job.get("salary"),
            "skills": job.get("skills") or [],
            "remote": bool(job.get("remote")),
            "h1b_approvals": job.get("h1b_approvals"),
        })
    return listings


def fetch_simplify() -> list:
    resp = requests.get(SIMPLIFY_LISTINGS_URL, timeout=60)
    resp.raise_for_status()
    return parse_simplify(json.loads(resp.content.decode("utf-8")), utc_today())


def fetch_zshah() -> list:
    resp = requests.get(ZSHAH_JOBS_URL, timeout=30)
    resp.raise_for_status()
    # requests' resp.json() can mis-detect encoding on this response (seen
    # producing mojibake on non-ASCII characters like em-dashes) - decode the
    # raw bytes as UTF-8 explicitly instead of trusting the guessed charset.
    return parse_zshah(json.loads(resp.content.decode("utf-8")), utc_today())


SOURCES = [fetch_simplify, fetch_zshah]
