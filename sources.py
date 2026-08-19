import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
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
# a .env override of GITHUB_REPO/GITHUB_BRANCH/README_PATH below would never
# take effect unless this module loads it too.
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


def date_from_age(age_text: str, today: date):
    age_text = age_text.strip()
    m = re.match(r"^(\d+)d$", age_text)
    if m:
        days = int(m.group(1))
        label = "today" if days == 0 else f"{days}d ago"
        return today - timedelta(days=days), days, label
    m = re.match(r"^(\d+)mo$", age_text)
    if m:
        months = int(m.group(1))
        days = months * 30
        return today - timedelta(days=days), days, f"~{months}mo ago"
    return None, None, ""


def age_from_date(posted: date, today: date):
    days = max((today - posted).days, 0)
    if days == 0:
        return 0, "today"
    if days < 30:
        return days, f"{days}d ago"
    months = round(days / 30)
    return days, f"~{months}mo ago"


def parse_vansh_date(date_text: str, today: date) -> date:
    parsed = datetime.strptime(f"{date_text.strip()} {today.year}", "%b %d %Y").date()
    if (parsed - today).days > 3:
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


def normalize_key(company: str, role: str) -> str:
    text = f"{company} {role}".lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Best-effort only: these README tables have no interview-process field at all,
# so this can only match if a role title/company literally names the tool
# (rare). It is not a real "no OA/no LeetCode" filter, just a free hint.
OA_LC_KEYWORDS = ("leetcode", "online assessment", "hackerrank", "codesignal", "coderpad")


def check_oa_lc(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in OA_LC_KEYWORDS)


# Bounds each section on ANY "## ..." header, not just ones that match the
# category pattern below — otherwise trailing content after a header format
# changes (or a non-category "##" section, e.g. a footer) silently gets
# folded into and misattributed to the *previous* matched category.
ANY_HEADER_RE = re.compile(r"^## .+$", re.MULTILINE)
CATEGORY_HEADER_RE = re.compile(r"^## [^\w]*([A-Za-z ,&]+) Internship Roles")


def split_by_category(markdown_text: str):
    boundaries = list(ANY_HEADER_RE.finditer(markdown_text))
    sections = []
    for i, m in enumerate(boundaries):
        header_text = m.group(0)
        header_match = CATEGORY_HEADER_RE.match(header_text)
        if not header_match:
            if "Internship Roles" in header_text:
                log.warning("Category header didn't match expected pattern, skipping section: %r", header_text)
            continue
        start = m.end()
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(markdown_text)
        sections.append((header_match.group(1).strip(), markdown_text[start:end]))
    return sections


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


def parse_simplify(markdown_text: str, today: date) -> list:
    listings = []
    for header_text, section in split_by_category(markdown_text):
        category = classify_category(header_text)
        soup = BeautifulSoup(section, "html.parser")
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
                age_cell = cells[4] if len(cells) > 4 else None

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
                apply_url = strip_tracking_params(apply_url)

                posted_date, age_days, age_label = None, None, ""
                if age_cell is not None:
                    posted_date, age_days, age_label = date_from_age(age_cell.get_text(strip=True), today)

                listings.append({
                    "company": company,
                    "role": role,
                    "location": location,
                    "apply_url": apply_url,
                    "category": category,
                    "posted_date": posted_date,
                    "age_days": age_days,
                    "age_label": age_label,
                    "oa_lc_flag": check_oa_lc(f"{company} {role}"),
                    "sponsorship_flag": False,
                    "citizenship_flag": False,
                    "source": "Simplify",
                })
    return listings


FLAG_SPONSORSHIP = "🛂"
FLAG_CITIZENSHIP = "🇺🇸"
FLAG_CLOSED = "🔒"


def parse_vansh(markdown_text: str, today: date) -> list:
    listings = []
    last_company = None
    in_table = False
    found_header = False
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0].strip("*_ ").lower() == "company":
            in_table = True
            found_header = True
            continue
        if not in_table:
            continue
        if re.match(r"^-+$", cells[0]):
            continue

        company_cell, role_cell, location_cell, apply_cell, date_cell = cells[:5]
        combined_flags = f"{company_cell} {role_cell}"

        company_text = BeautifulSoup(company_cell, "html.parser").get_text(strip=True)
        if company_text in ("↳", ""):
            company = last_company
        else:
            company = re.sub(r"^[^\w]+", "", company_text).strip()
            last_company = company
        if not company:
            continue

        if FLAG_CLOSED in combined_flags:
            continue

        role = BeautifulSoup(role_cell, "html.parser").get_text(strip=True)
        role = re.sub(f"[{FLAG_SPONSORSHIP}{FLAG_CITIZENSHIP}]", "", role)
        role = re.sub(r"\s+", " ", role).strip()

        location_soup = BeautifulSoup(location_cell, "html.parser")
        summary = location_soup.find("summary")
        # .strip("*") because the source wraps the summary text in markdown
        # bold ("**2 locations**"), which get_text() doesn't strip on its own.
        location = summary.get_text(strip=True).strip("*") if summary else (", ".join(location_soup.stripped_strings) or "N/A")

        apply_link = BeautifulSoup(apply_cell, "html.parser").find("a")
        apply_url = apply_link["href"] if apply_link and apply_link.has_attr("href") else None
        if not apply_url:
            continue
        apply_url = strip_tracking_params(apply_url)

        posted_date, age_days, age_label = None, None, ""
        date_text = date_cell.strip()
        if date_text:
            try:
                posted_date = parse_vansh_date(date_text, today)
                age_days, age_label = age_from_date(posted_date, today)
            except ValueError:
                pass

        listings.append({
            "company": company,
            "role": role,
            "location": location,
            "apply_url": apply_url,
            "category": classify_category(role),
            "posted_date": posted_date,
            "age_days": age_days,
            "age_label": age_label,
            "oa_lc_flag": check_oa_lc(f"{company} {role}"),
            "sponsorship_flag": FLAG_SPONSORSHIP in combined_flags,
            "citizenship_flag": FLAG_CITIZENSHIP in combined_flags,
            "source": "Vansh",
        })

    if not found_header:
        log.warning("vansh parser never found a 'Company' header row — source format may have changed")
    return listings


GITHUB_REPO = os.getenv("GITHUB_REPO", "SimplifyJobs/Summer2027-Internships")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "dev")
README_PATH = os.getenv("README_PATH", "README.md")
SIMPLIFY_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{README_PATH}"
VANSH_RAW_URL = "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md"
ZSHAH_JOBS_URL = "https://raw.githubusercontent.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/docs/api/jobs.json"

# This source scrapes raw ATS postings (Greenhouse/Workday/etc.) rather than
# being human-curated like the other two, so it also carries non-internship
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

        posted_date, age_days, age_label = None, None, ""
        posted_at = job.get("posted_at")
        if posted_at:
            try:
                posted_date = datetime.fromisoformat(posted_at).astimezone(timezone.utc).date()
                age_days, age_label = age_from_date(posted_date, today)
            except ValueError:
                pass

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
            "age_days": age_days,
            "age_label": age_label,
            "oa_lc_flag": check_oa_lc(f"{company} {role}"),
            "sponsorship_flag": sponsorship == "no-sponsorship",
            "citizenship_flag": sponsorship == "citizens-only",
            "source": "Zshah",
            "program": program,
            "salary": job.get("salary"),
            "skills": job.get("skills") or [],
            "remote": bool(job.get("remote")),
            "h1b_approvals": job.get("h1b_approvals"),
        })
    return listings


def fetch_simplify() -> list:
    resp = requests.get(SIMPLIFY_RAW_URL, timeout=30)
    resp.raise_for_status()
    return parse_simplify(resp.text, utc_today())


def fetch_vansh() -> list:
    resp = requests.get(VANSH_RAW_URL, timeout=30)
    resp.raise_for_status()
    return parse_vansh(resp.text, utc_today())


def fetch_zshah() -> list:
    resp = requests.get(ZSHAH_JOBS_URL, timeout=30)
    resp.raise_for_status()
    # requests' resp.json() can mis-detect encoding on this response (seen
    # producing mojibake on non-ASCII characters like em-dashes) - decode the
    # raw bytes as UTF-8 explicitly instead of trusting the guessed charset.
    return parse_zshah(json.loads(resp.content.decode("utf-8")), utc_today())


SOURCES = [fetch_simplify, fetch_vansh, fetch_zshah]
