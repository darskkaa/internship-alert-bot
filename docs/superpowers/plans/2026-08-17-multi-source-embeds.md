# Multi-Source Tracking + Richer Embeds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second internship-listing source (vanshb03/Summer2027-Internships) alongside the existing SimplifyJobs source, and enrich every Discord alert with category, posted date, freshness ("age"), and sponsorship/citizenship flags — computing whichever of those each source doesn't natively provide.

**Architecture:** New `sources.py` module holds per-source parsers that each return a list of dicts in one common schema (company/role/location/apply_url/category/posted_date/age_days/age_label/oa_lc_flag/sponsorship_flag/citizenship_flag/source). `core.py`'s `run_once()` loops over a `SOURCES` list of fetch functions, merges results, dedupes against `state.json` using both the existing per-URL set and a new cross-source normalized-key set, and posts new listings as structured Discord embeds.

**Tech Stack:** Python 3.11, requests, beautifulsoup4 (also used to strip HTML fragments out of vansh's markdown cells), pytest (dev-only, new).

Spec: `docs/superpowers/specs/2026-08-17-multi-source-embeds-design.md`

---

## Task 1: Shared pure helpers — date inference, categorization, dedup key

**Files:**
- Create: `sources.py`
- Create: `tests/conftest.py`
- Test: `tests/test_sources.py`

- [ ] **Step 1: Create `tests/conftest.py` so importing `core` in later tests doesn't crash on missing env**

```python
import os

os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/test")
```

- [ ] **Step 2: Write failing tests for the pure helpers**

Create `tests/test_sources.py`:

```python
from datetime import date

from sources import (
    age_from_date,
    classify_category,
    date_from_age,
    normalize_key,
    parse_vansh_date,
)


def test_date_from_age_days():
    posted, days, label = date_from_age("3d", date(2026, 8, 17))
    assert posted == date(2026, 8, 14)
    assert days == 3
    assert label == "3d ago"


def test_date_from_age_today():
    posted, days, label = date_from_age("0d", date(2026, 8, 17))
    assert posted == date(2026, 8, 17)
    assert days == 0
    assert label == "today"


def test_date_from_age_months():
    posted, days, label = date_from_age("1mo", date(2026, 8, 17))
    assert posted == date(2026, 7, 18)
    assert days == 30
    assert label == "~1mo ago"


def test_date_from_age_unrecognized():
    posted, days, label = date_from_age("garbage", date(2026, 8, 17))
    assert posted is None
    assert days is None
    assert label == ""


def test_age_from_date_recent():
    days, label = age_from_date(date(2026, 8, 14), date(2026, 8, 17))
    assert days == 3
    assert label == "3d ago"


def test_age_from_date_today():
    days, label = age_from_date(date(2026, 8, 17), date(2026, 8, 17))
    assert days == 0
    assert label == "today"


def test_age_from_date_over_a_month():
    days, label = age_from_date(date(2026, 6, 1), date(2026, 8, 17))
    assert days == 77
    assert label == "~3mo ago"


def test_parse_vansh_date_same_year():
    assert parse_vansh_date("Aug 14", date(2026, 8, 17)) == date(2026, 8, 14)


def test_parse_vansh_date_rolls_back_year_when_naive_parse_lands_in_future():
    # A late-December post ("Dec 30") viewed shortly after New Year's
    # ("today" is Jan 3 of the *next* year) would parse as Dec 30 of
    # *this* year if we naively used today.year — ~362 days in the
    # future, which can't be a real "posted" date. Must roll back to
    # last year, landing 4 days in the past instead.
    assert parse_vansh_date("Dec 30", date(2027, 1, 3)) == date(2026, 12, 30)


def test_classify_category_quant():
    assert classify_category("Quantitative Trading Intern") == "Quantitative Finance"


def test_classify_category_data():
    assert classify_category("AI Engineer Intern - Enterprise Technology Services") == "Data Science"


def test_classify_category_product():
    assert classify_category("Product Manager Intern - Content and Services") == "Product Management"


def test_classify_category_hardware():
    assert classify_category("ASIC Design Engineer Intern - Video Silicon IP") == "Hardware Engineering"


def test_classify_category_swe_default_for_generic_engineer_titles():
    assert classify_category("Software Engineer Intern - Summer 2027") == "Software Engineering"


def test_classify_category_other_fallback():
    assert classify_category("Corporate Summer Internship - Facilities") == "Other"


def test_normalize_key_strips_punctuation_and_case():
    assert normalize_key("Acme, Inc.", "SWE Intern!!") == normalize_key("acme inc", "swe intern")


def test_normalize_key_differs_for_different_roles():
    assert normalize_key("Acme", "SWE Intern") != normalize_key("Acme", "PM Intern")
```

- [ ] **Step 3: Run tests, confirm they fail on missing module**

Run: `pytest tests/test_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sources'`

- [ ] **Step 4: Implement the helpers in `sources.py`**

```python
import re
from datetime import date, datetime, timedelta


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
```

- [ ] **Step 5: Run tests, confirm they pass**

Run: `pytest tests/test_sources.py -v`
Expected: PASS (17 tests)

- [ ] **Step 6: Commit**

```bash
git add sources.py tests/conftest.py tests/test_sources.py
git commit -m "feat: add date/category/dedup-key inference helpers"
```

---

## Task 2: SimplifyJobs parser — category sections + date/age

**Files:**
- Modify: `sources.py`
- Modify: `tests/test_sources.py`

- [ ] **Step 1: Write failing test for the Simplify parser using an inline fixture**

Append to `tests/test_sources.py`:

```python
from datetime import date as _date

from sources import parse_simplify

SIMPLIFY_FIXTURE = """
## 💻 Software Engineering Internship Roles

<table>
<thead>
<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
</thead>
<tbody>
<tr>
<td><strong><a href="https://simplify.jobs/c/Acme">Acme</a></strong></td>
<td>Software Engineer Intern</td>
<td>Remote</td>
<td><a href="https://acme.example/apply?utm_source=GHList&utm_medium=company&jobId=42">Apply</a></td>
<td>2d</td>
</tr>
<tr>
<td>↳</td>
<td>Backend Engineer Intern - LeetCode round required</td>
<td>NYC</td>
<td><a href="https://acme.example/apply2">Apply</a></td>
<td>2d</td>
</tr>
</tbody>
</table>

## 📈 Quantitative Finance Internship Roles

<table>
<thead>
<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
</thead>
<tbody>
<tr>
<td><strong><a href="https://simplify.jobs/c/QuantCo">QuantCo</a></strong></td>
<td>Quant Trading Intern</td>
<td>Chicago, IL</td>
<td><a href="https://quantco.example/apply">Apply</a></td>
<td>0d</td>
</tr>
</tbody>
</table>
"""


def test_parse_simplify_extracts_category_from_section():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    categories = {item["company"]: item["category"] for item in listings}
    assert categories["Acme"] == "Software Engineering"
    assert categories["QuantCo"] == "Quantitative Finance"


def test_parse_simplify_resolves_company_continuation():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    acme_roles = [item["role"] for item in listings if item["company"] == "Acme"]
    assert "Backend Engineer Intern - LeetCode round required" in acme_roles


def test_parse_simplify_infers_date_from_age():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    quantco = next(item for item in listings if item["company"] == "QuantCo")
    assert quantco["posted_date"] == _date(2026, 8, 17)
    assert quantco["age_label"] == "today"


def test_parse_simplify_flags_oa_lc_keyword():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    backend = next(item for item in listings if "Backend" in item["role"])
    assert backend["oa_lc_flag"] is True


def test_parse_simplify_sets_source_and_no_vansh_only_flags():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    for item in listings:
        assert item["source"] == "Simplify"
        assert item["sponsorship_flag"] is False
        assert item["citizenship_flag"] is False


def test_parse_simplify_strips_tracking_params_but_keeps_functional_ones():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme" and "Software" in item["role"])
    assert acme["apply_url"] == "https://acme.example/apply?jobId=42"


TRAILING_SECTION_FIXTURE = SIMPLIFY_FIXTURE + """

## Off-Season / Archived

<table>
<thead>
<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th><th>Age</th></tr>
</thead>
<tbody>
<tr>
<td><strong><a href="https://simplify.jobs/c/OldCo">OldCo</a></strong></td>
<td>Archived Intern Role</td>
<td>Remote</td>
<td><a href="https://oldco.example/apply">Apply</a></td>
<td>1mo</td>
</tr>
</tbody>
</table>
"""


def test_parse_simplify_does_not_leak_trailing_non_category_section_into_last_category():
    listings = parse_simplify(TRAILING_SECTION_FIXTURE, _date(2026, 8, 17))
    companies = {item["company"] for item in listings}
    assert "OldCo" not in companies
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_sources.py -v -k parse_simplify`
Expected: FAIL — `ImportError: cannot import name 'parse_simplify'`

- [ ] **Step 3: Implement `parse_simplify` in `sources.py`**

Add to `sources.py` (needs `from bs4 import BeautifulSoup` and `from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit` at the top, plus the OA/LC constant moved in from the old `core.py`):

```python
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

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


# Best-effort only: these README tables have no interview-process field at all,
# so this can only match if a role title/company literally names the tool
# (rare). It is not a real "no OA/no LeetCode" filter, just a free hint.
OA_LC_KEYWORDS = ("leetcode", "online assessment", "hackerrank", "codesignal", "coderpad")


def check_oa_lc(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in OA_LC_KEYWORDS)


import logging

log = logging.getLogger("internship-watch.sources")

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
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_sources.py -v -k parse_simplify`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add sources.py tests/test_sources.py
git commit -m "feat: parse Simplify categories and age from source data"
```

---

## Task 3: vansh parser — markdown table, date, sponsorship/citizenship/closed flags

**Files:**
- Modify: `sources.py`
- Modify: `tests/test_sources.py`

- [ ] **Step 1: Write failing tests using an inline vansh-format fixture**

Append to `tests/test_sources.py`:

```python
from sources import parse_vansh

VANSH_FIXTURE = """
| Company | Role | Location | Application/Link | Date Posted |
| ------- | ---- | -------- | ---------------- | ----------- |
| Acme | Software Engineer Intern 🇺🇸 | Remote | <a href="https://acme.example/apply?utm_source=github-vansh-ouckah&role=42">Apply</a> | Aug 16 |
| ↳ | Backend Engineer Intern 🛂 | NYC | <a href="https://acme.example/apply2">Apply</a> | Aug 16 |
| Closed Co | Some Intern 🔒 | Remote | <a href="https://closedco.example/apply">Apply</a> | Aug 10 |
| <details><summary>**2 locations**</summary>Chicago, IL</br>Boston, MA</details> ignored | Quant Intern | <details><summary>**2 locations**</summary>Chicago, IL</br>Boston, MA</details> | <a href="https://multi.example/apply">Apply</a> | Aug 15 |
"""


def test_parse_vansh_extracts_flags():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme")
    assert acme["citizenship_flag"] is True
    assert acme["sponsorship_flag"] is False
    backend = next(item for item in listings if "Backend" in item["role"])
    assert backend["sponsorship_flag"] is True
    assert backend["company"] == "Acme"  # continuation via ↳


def test_parse_vansh_drops_closed_listings():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    companies = {item["company"] for item in listings}
    assert "Closed Co" not in companies


def test_parse_vansh_infers_age_from_date():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme")
    assert acme["posted_date"] == _date(2026, 8, 16)
    assert acme["age_label"] == "1d ago"


def test_parse_vansh_categorizes_by_role_keyword():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    quant = next(item for item in listings if "Quant" in item["role"])
    assert quant["category"] == "Quantitative Finance"


def test_parse_vansh_strips_multi_location_to_summary():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    quant = next(item for item in listings if "Quant" in item["role"])
    assert quant["location"] == "2 locations"


def test_parse_vansh_sets_source():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    assert all(item["source"] == "Vansh" for item in listings)


def test_parse_vansh_strips_tracking_params_but_keeps_functional_ones():
    listings = parse_vansh(VANSH_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme" and "Software" in item["role"])
    assert acme["apply_url"] == "https://acme.example/apply?role=42"


CLOSED_THEN_OPEN_CONTINUATION_FIXTURE = """
| Company | Role | Location | Application/Link | Date Posted |
| ------- | ---- | -------- | ---------------- | ----------- |
| CompanyA | SWE Intern | Remote | <a href="https://a.example/apply">Apply</a> | Aug 16 |
| CompanyB | Closed Role Intern 🔒 | Remote | <a href="https://b1.example/apply">Apply</a> | Aug 16 |
| ↳ | Open Role Intern | NYC | <a href="https://b2.example/apply">Apply</a> | Aug 15 |
"""


def test_parse_vansh_continuation_after_closed_row_keeps_correct_company():
    listings = parse_vansh(CLOSED_THEN_OPEN_CONTINUATION_FIXTURE, _date(2026, 8, 17))
    open_role = next(item for item in listings if "Open Role" in item["role"])
    assert open_role["company"] == "CompanyB"
    assert not any("Closed Role" in item["role"] for item in listings)


def test_parse_vansh_handles_missing_or_malformed_date_gracefully():
    fixture = """
| Company | Role | Location | Application/Link | Date Posted |
| ------- | ---- | -------- | ---------------- | ----------- |
| WeirdCo | SWE Intern | Remote | <a href="https://weirdco.example/apply">Apply</a> | TBD |
"""
    listings = parse_vansh(fixture, _date(2026, 8, 17))
    weirdco = next(item for item in listings if item["company"] == "WeirdCo")
    assert weirdco["posted_date"] is None
    assert weirdco["age_days"] is None
    assert weirdco["age_label"] == ""
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_sources.py -v -k parse_vansh`
Expected: FAIL — `ImportError: cannot import name 'parse_vansh'`

- [ ] **Step 3: Implement `parse_vansh` in `sources.py`**

```python
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
        # strip("*_ ") tolerates a markdown-bold-wrapped header ("**Company**")
        # without also matching ordinary data rows whose company name happens
        # to contain "company" as a substring (e.g. "CompanyA").
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

        # Company resolution happens before the closed-flag check below, so
        # last_company is always correct even when this particular row is
        # closed — otherwise a later open ↳ continuation for the same
        # company would silently inherit whatever company came before it.
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
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_sources.py -v -k parse_vansh`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add sources.py tests/test_sources.py
git commit -m "feat: add vansh markdown-table parser with sponsorship/citizenship/closed flags"
```

---

## Task 4: Network wrappers and the `SOURCES` registry

**Files:**
- Modify: `sources.py`

- [ ] **Step 1: Add fetch wrappers and the registry (no test — thin network I/O, exercised by the Task 6 dry-run and Task 9 live check)**

Add to `sources.py`:

```python
import os
import requests
from dotenv import load_dotenv

# sources.py is imported by core.py before core.py calls load_dotenv(), so
# a .env override of GITHUB_REPO/GITHUB_BRANCH/README_PATH below would never
# take effect unless this module loads it too.
load_dotenv()

GITHUB_REPO = os.getenv("GITHUB_REPO", "SimplifyJobs/Summer2027-Internships")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "dev")
README_PATH = os.getenv("README_PATH", "README.md")
SIMPLIFY_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{README_PATH}"
VANSH_RAW_URL = "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md"


def fetch_simplify() -> list:
    resp = requests.get(SIMPLIFY_RAW_URL, timeout=30)
    resp.raise_for_status()
    return parse_simplify(resp.text, date.today())


def fetch_vansh() -> list:
    resp = requests.get(VANSH_RAW_URL, timeout=30)
    resp.raise_for_status()
    return parse_vansh(resp.text, date.today())


SOURCES = [fetch_simplify, fetch_vansh]
```

- [ ] **Step 2: Sanity-check imports resolve**

Run: `python -c "from sources import SOURCES; print([f.__name__ for f in SOURCES])"`
Expected: `['fetch_simplify', 'fetch_vansh']`

- [ ] **Step 3: Commit**

```bash
git add sources.py
git commit -m "feat: wire fetch_simplify/fetch_vansh into a SOURCES registry"
```

---

## Task 5: `core.py` state — add cross-source `seen_keys`

**Files:**
- Modify: `core.py:1-52` (imports, `load_state`, `save_state`)
- Test: `tests/test_core.py`

- [ ] **Step 1: Write failing tests for `load_state`/`save_state`**

Create `tests/test_core.py`:

```python
import json

import core


def test_load_state_defaults_seen_keys_when_missing(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"seen": ["https://a.example"], "last_checked_utc": None}))
    monkeypatch.setattr(core, "STATE_FILE", state_file)

    state = core.load_state()

    assert state["seen"] == ["https://a.example"]
    assert state["seen_keys"] == []


def test_load_state_no_file_gives_empty_seen_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "STATE_FILE", tmp_path / "missing.json")

    state = core.load_state()

    assert state == {"seen": [], "seen_keys": [], "last_checked_utc": None}


def test_load_state_migrates_old_list_only_format(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(["https://a.example"]))
    monkeypatch.setattr(core, "STATE_FILE", state_file)

    state = core.load_state()

    assert state == {"seen": ["https://a.example"], "seen_keys": [], "last_checked_utc": None}


def test_save_state_persists_seen_keys(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(core, "STATE_FILE", state_file)

    core.save_state({"seen": ["x"], "seen_keys": ["acme swe intern"]})

    saved = json.loads(state_file.read_text())
    assert saved["seen_keys"] == ["acme swe intern"]
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_core.py -v`
Expected: FAIL — `test_load_state_defaults_seen_keys_when_missing` and others assert on a `seen_keys` key that doesn't exist yet

- [ ] **Step 3: Update `load_state` in `core.py`**

Replace `core.py:40-46`:

```python
def load_state() -> dict:
    if STATE_FILE.exists():
        data = json.loads(STATE_FILE.read_text())
        if isinstance(data, list):  # migrate from the old list-only format
            return {"seen": data, "seen_keys": [], "last_checked_utc": None}
        data.setdefault("seen_keys", [])
        return data
    return {"seen": [], "seen_keys": [], "last_checked_utc": None}
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_core.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "feat: add seen_keys to state for cross-source dedup"
```

---

## Task 6: `core.py` `run_once` — multi-source merge, dual dedup, per-source error isolation

**Files:**
- Modify: `core.py` (imports, `run_once`; remove old `fetch_readme`/`parse_listings`/`OA_LC_KEYWORDS`/`check_oa_lc`/`GITHUB_REPO`/`GITHUB_BRANCH`/`README_PATH`/`RAW_URL`, now in `sources.py`)
- Modify: `tests/test_core.py`

- [ ] **Step 1: Write failing tests for `run_once` against fake sources**

Append to `tests/test_core.py`:

```python
import core


def _fake_listing(company, role, url, source="Simplify"):
    return {
        "company": company,
        "role": role,
        "location": "Remote",
        "apply_url": url,
        "category": "Software Engineering",
        "posted_date": None,
        "age_days": 0,
        "age_label": "today",
        "oa_lc_flag": False,
        "sponsorship_flag": False,
        "citizenship_flag": False,
        "source": source,
    }


def test_run_once_first_run_seeds_without_posting(monkeypatch):
    listing = _fake_listing("Acme", "SWE Intern", "https://acme.example/1")
    monkeypatch.setattr(core, "SOURCES", [lambda: [listing]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": [], "seen_keys": [], "last_checked_utc": None})

    assert state["seen"] == ["https://acme.example/1"]
    assert state["seen_keys"] == [core.normalize_key("Acme", "SWE Intern")]
    assert posted == []


def test_run_once_dedupes_same_role_posted_on_two_sources(monkeypatch):
    a = _fake_listing("Acme", "SWE Intern", "https://simplify.example/acme", source="Simplify")
    b = _fake_listing("Acme", "SWE Intern", "https://vansh.example/acme", source="Vansh")
    monkeypatch.setattr(core, "SOURCES", [lambda: [a], lambda: [b]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    key = core.normalize_key("Acme", "SWE Intern")
    state = core.run_once({"seen": [], "seen_keys": [key], "last_checked_utc": None})

    assert posted == []  # already-seen key, even though both URLs are new


def test_run_once_posts_genuinely_new_listing(monkeypatch):
    listing = _fake_listing("NewCo", "SWE Intern", "https://newco.example/1")
    monkeypatch.setattr(core, "SOURCES", [lambda: [listing]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://other.example"], "seen_keys": ["other key"], "last_checked_utc": "x"})

    assert posted == [listing]
    assert "https://newco.example/1" in state["seen"]


def test_run_once_continues_when_one_source_raises(monkeypatch):
    def broken():
        raise RuntimeError("source down")

    good_listing = _fake_listing("Acme", "SWE Intern", "https://acme.example/1")
    monkeypatch.setattr(core, "SOURCES", [broken, lambda: [good_listing]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert posted == [good_listing]


def test_run_once_posts_new_listings_newest_first(monkeypatch):
    from datetime import date as _date

    older = _fake_listing("OldCo", "SWE Intern", "https://oldco.example/1")
    older["posted_date"] = _date(2026, 8, 10)
    newer = _fake_listing("NewCo", "SWE Intern", "https://newco.example/1")
    newer["posted_date"] = _date(2026, 8, 16)
    dateless = _fake_listing("NoDateCo", "SWE Intern", "https://nodateco.example/1")
    dateless["posted_date"] = None

    # Sources return them out of order on purpose, to prove run_once sorts.
    monkeypatch.setattr(core, "SOURCES", [lambda: [older, dateless, newer]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert [item["company"] for item in posted] == ["NewCo", "OldCo", "NoDateCo"]
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_core.py -v -k run_once`
Expected: FAIL — `AttributeError: module 'core' has no attribute 'SOURCES'` (and `normalize_key` not imported into `core`)

- [ ] **Step 3: Replace imports and `run_once` in `core.py`**

Replace `core.py:1-37` — everything from the top of the file through the
`check_oa_lc` function (this removes `GITHUB_REPO`/`GITHUB_BRANCH`/
`README_PATH`/`RAW_URL` and `OA_LC_KEYWORDS`/`check_oa_lc`, all of which now
live in `sources.py` as of Task 2 — leaving them in `core.py` too would be
dead duplicate code) — with:

```python
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

STATE_FILE = Path(__file__).parent / "state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("internship-watch")
```

Replace `run_once` (old `core.py:131-149`) with:

```python
REQUIRED_LISTING_KEYS = {
    "company", "role", "location", "apply_url", "category",
    "posted_date", "age_days", "age_label", "oa_lc_flag",
    "sponsorship_flag", "citizenship_flag", "source",
}


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

    # Dedup incrementally against the batch itself (posted_keys), not just
    # pre-existing state — otherwise the same role appearing on two sources
    # for the first time in the same cycle would pass both checks twice and
    # get posted twice, since neither copy is in `seen`/`seen_keys` yet.
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
```

Also delete the now-unused `fetch_readme` and `parse_listings` functions (old `core.py:54-100`) — that logic now lives in `sources.py` as `parse_simplify`/`fetch_simplify`.

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_core.py -v -k run_once`
Expected: PASS (8 tests — includes intra-cycle dedup, malformed-item isolation, and post-migration-asymmetric-state regression tests added after code review)

- [ ] **Step 5: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "feat: merge multi-source listings with dual dedup in run_once"
```

---

## Task 7: Structured embed builder

**Files:**
- Modify: `core.py` (`post_new_listings`)
- Modify: `tests/test_core.py`

- [ ] **Step 1: Write failing tests for `build_embed`**

Append to `tests/test_core.py`:

```python
from datetime import date

from core import build_embed


def _item(**overrides):
    base = {
        "company": "Acme",
        "role": "SWE Intern",
        "location": "Remote",
        "apply_url": "https://acme.example/1",
        "category": "Software Engineering",
        "posted_date": date(2026, 8, 14),
        "age_days": 3,
        "age_label": "3d ago",
        "oa_lc_flag": False,
        "sponsorship_flag": False,
        "citizenship_flag": False,
        "source": "Simplify",
    }
    base.update(overrides)
    return base


def test_build_embed_basic_fields():
    embed = build_embed(_item())
    assert embed["title"] == "Acme — SWE Intern"
    assert embed["url"] == "https://acme.example/1"
    field_names = [f["name"] for f in embed["fields"]]
    assert "📍 Location" in field_names
    assert "🏷️ Category" in field_names
    assert "📅 Posted" in field_names


def test_build_embed_posted_field_shows_date_and_age():
    embed = build_embed(_item())
    posted_field = next(f for f in embed["fields"] if f["name"] == "📅 Posted")
    assert posted_field["value"] == "Aug 14, 2026 · 3d ago"


def test_build_embed_oa_lc_warning_only_when_flagged():
    embed = build_embed(_item(oa_lc_flag=True))
    assert any(f["name"] == "⚠️ Heads up" for f in embed["fields"])
    embed = build_embed(_item(oa_lc_flag=False))
    assert not any(f["name"] == "⚠️ Heads up" for f in embed["fields"])


def test_build_embed_sponsorship_and_citizenship_fields_optional():
    embed = build_embed(_item(sponsorship_flag=True, citizenship_flag=True))
    names = [f["name"] for f in embed["fields"]]
    assert "🛂" in names
    assert "🇺🇸" in names
    embed = build_embed(_item())
    names = [f["name"] for f in embed["fields"]]
    assert "🛂" not in names
    assert "🇺🇸" not in names


def test_build_embed_footer_names_source():
    embed = build_embed(_item(source="Vansh"))
    assert embed["footer"]["text"] == "via Vansh"


def test_build_embed_includes_native_discord_timestamp():
    embed = build_embed(_item(posted_date=date(2026, 8, 14)))
    assert embed["timestamp"] == "2026-08-14T00:00:00+00:00"


def test_build_embed_omits_timestamp_when_posted_date_missing():
    embed = build_embed(_item(posted_date=None))
    assert "timestamp" not in embed


def test_build_embed_truncates_overlong_title():
    long_role = "X" * 300
    embed = build_embed(_item(role=long_role))
    assert len(embed["title"]) == 256
    assert embed["title"].endswith("…")


def test_build_embed_truncates_overlong_location():
    long_location = "Y" * 2000
    embed = build_embed(_item(location=long_location))
    location_field = next(f for f in embed["fields"] if f["name"] == "📍 Location")
    assert len(location_field["value"]) == 1024
    assert location_field["value"].endswith("…")
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_core.py -v -k build_embed`
Expected: FAIL — `ImportError: cannot import name 'build_embed'`

- [ ] **Step 3: Add `build_embed` and rewrite `post_new_listings` in `core.py`**

Replace the old `post_new_listings` (old `core.py:103-128`) with:

```python
def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_embed(item: dict) -> dict:
    # Discord caps embed title at 256 chars, field values at 1024 — company/
    # role/location come from scraped third-party READMEs, unbounded, so
    # truncate defensively rather than risk a rejected POST wedging the
    # whole batch (category/posted-value are always short, controlled
    # strings and don't need it).
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
        # "📅 Posted" field above. A bare date.isoformat() ("2026-08-14") is
        # technically valid ISO8601 but ambiguous against Discord's webhook
        # validator — use an explicit UTC midnight datetime instead so
        # there's no doubt.
        embed["timestamp"] = datetime.combine(item["posted_date"], dtime.min, tzinfo=timezone.utc).isoformat()
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
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_core.py -v -k build_embed`
Expected: PASS (9 tests — includes truncation-defense tests added after code review; timestamp test asserts a full ISO8601 datetime, not a bare date)

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: PASS (all tests across `test_sources.py` and `test_core.py`)

- [ ] **Step 6: Commit**

```bash
git add core.py tests/test_core.py
git commit -m "feat: structured Discord embeds with category/date/flags"
```

---

## Task 8: Dev dependencies and docs

**Files:**
- Create: `requirements-dev.txt`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Add dev requirements**

Create `requirements-dev.txt`:

```
-r requirements.txt
pytest
```

- [ ] **Step 2: Document the second source and richer embeds in `README.md`**

Add a section after "## First run" in `README.md`:

```markdown
## Sources

Tracks two listing repos and merges/dedupes them:

- [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) — category and posting age come directly from this source.
- [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships) — posting date comes directly from this source; category is inferred from the role title. Closed listings (🔒 in the source) are never posted. Sponsorship (🛂) and citizenship (🇺🇸) flags, when present, are shown on the alert.

The same internship posted on both trackers is only alerted once — matched by
normalized company + role text, not just the application link (the two
trackers often link to different application portals for the same job).

Each alert shows: location, category, posted date + freshness ("3d ago"),
and any OA/LeetCode, sponsorship, or citizenship flags the source data
supports.

## Running tests

```
pip install -r requirements-dev.txt
pytest
```
```

- [ ] **Step 3: Note the dev-only dependency in `.env.example`'s neighbor context (no code change needed — `GITHUB_REPO`/`GITHUB_BRANCH`/`README_PATH` still only apply to the Simplify source)**

Read `.env.example` and confirm no changes are needed there (vansh's URL is fixed in `sources.py`, not configurable — no new env var). No edit required; this step is a verification, not a change.

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt README.md
git commit -m "docs: document multi-source tracking and test setup"
```

---

## Task 9: Live dry-run verification

**Files:**
- Create (scratch, not committed): a throwaway dry-run invocation — no new repo file needed since `sources.py` functions are directly callable.

- [ ] **Step 1: Run a live dry-run against both real sources without posting or touching `state.json`**

Run:

```bash
python -c "
from sources import SOURCES
from core import normalize_key

listings = []
for fetch in SOURCES:
    items = fetch()
    print(fetch.__name__, len(items))
    listings.extend(items)

print('total', len(listings))
print('unique urls', len({i[\"apply_url\"] for i in listings}))
print('unique keys', len({normalize_key(i[\"company\"], i[\"role\"]) for i in listings}))
"
```

Expected: both fetchers return non-zero counts; `unique keys` is somewhat lower than `total` (cross-source overlap collapsing), no exceptions, nothing posted (this script never calls `post_new_listings`).

- [ ] **Step 2: Confirm `state.json` is untouched**

Run: `git status --short`
Expected: no changes to `state.json` (the dry-run above never calls `save_state`)

- [ ] **Step 3: Run the full automated suite one more time as a final gate**

Run: `pytest -v`
Expected: PASS, all tests

- [ ] **Step 4: Commit is not needed for this task (verification only) — proceed to finishing-a-development-branch to decide how this lands (push, PR, etc.)**
