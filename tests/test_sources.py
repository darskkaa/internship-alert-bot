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


def test_parse_simplify_strips_tracking_params_but_keeps_functional_ones():
    listings = parse_simplify(SIMPLIFY_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme" and "Software" in item["role"])
    assert acme["apply_url"] == "https://acme.example/apply?jobId=42"


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
    assert backend["role"] == "Backend Engineer Intern"  # no leftover double space from flag stripping


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
    # the closed row itself must never appear
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


BOLD_HEADER_FIXTURE = """
| **Company** | **Role** | **Location** | **Application/Link** | **Date Posted** |
| ------- | ---- | -------- | ---------------- | ----------- |
| BoldCo | SWE Intern | Remote | <a href="https://boldco.example/apply">Apply</a> | Aug 16 |
"""


def test_parse_vansh_tolerates_markdown_bold_header():
    listings = parse_vansh(BOLD_HEADER_FIXTURE, _date(2026, 8, 17))
    assert any(item["company"] == "BoldCo" for item in listings)


def test_parse_vansh_logs_warning_when_no_header_row_found(caplog):
    fixture = """
| Some Random Table | Not What We Expect |
| ------- | ---- |
| foo | bar |
"""
    with caplog.at_level("WARNING"):
        listings = parse_vansh(fixture, _date(2026, 8, 17))
    assert listings == []
    assert any("header row" in record.message for record in caplog.records)


from sources import parse_zshah

ZSHAH_FIXTURE = {
    "jobs": [
        {
            "company": "Snorkel AI",
            "title": "AI Researcher Intern",
            "location": "New York City, NY (Hybrid)",
            "url": "https://job-boards.greenhouse.io/snorkelai/jobs/1?utm_source=x",
            "posted_at": "2026-08-14T17:35:22-04:00",
            "sponsorship": "no-sponsorship",
            "program": "Internship",
        },
        {
            "company": "Acme Corp",
            "title": "Data Engineer Co-op",
            "location": "Remote",
            "url": "https://job-boards.greenhouse.io/acme/jobs/2",
            "posted_at": "2026-08-16T00:00:00Z",
            "sponsorship": "citizens-only",
            "program": "Co-op",
        },
        {
            "company": "FullTimeCo",
            "title": "Senior Software Engineer",
            "location": "Remote",
            "url": "https://job-boards.greenhouse.io/fulltimeco/jobs/3",
            "posted_at": "2026-08-16T00:00:00Z",
            "sponsorship": "unknown",
            "program": "Full-time",
        },
        {
            "company": "NoDateCo",
            "title": "Quant Researcher Intern",
            "location": "Chicago, IL",
            "url": "https://job-boards.greenhouse.io/nodateco/jobs/4",
            "posted_at": None,
            "sponsorship": "offers",
            "program": "Internship",
        },
    ]
}


def test_parse_zshah_maps_core_fields():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    assert snorkel["role"] == "AI Researcher Intern"
    assert snorkel["location"] == "New York City, NY (Hybrid)"
    assert snorkel["source"] == "Zshah"


def test_parse_zshah_drops_non_internship_programs():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    assert not any(item["company"] == "FullTimeCo" for item in listings)


def test_parse_zshah_keeps_coop_and_tags_program():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme Corp")
    assert acme["program"] == "Co-op"


def test_parse_zshah_uses_exact_posted_at_timestamp():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    # 2026-08-14T17:35:22-04:00 -> 21:35:22 UTC -> still Aug 14 UTC.
    assert snorkel["posted_date"] == _date(2026, 8, 14)
    assert snorkel["age_days"] == 3


def test_parse_zshah_maps_sponsorship_enum_to_flags():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    acme = next(item for item in listings if item["company"] == "Acme Corp")
    nodateco = next(item for item in listings if item["company"] == "NoDateCo")
    assert snorkel["sponsorship_flag"] is True and snorkel["citizenship_flag"] is False
    assert acme["sponsorship_flag"] is False and acme["citizenship_flag"] is True
    assert nodateco["sponsorship_flag"] is False and nodateco["citizenship_flag"] is False


def test_parse_zshah_categorizes_by_role_keyword():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme Corp")
    assert acme["category"] == "Data Science"


def test_parse_zshah_strips_tracking_params():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    assert "utm_source" not in snorkel["apply_url"]


def test_parse_zshah_handles_missing_posted_at():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    nodateco = next(item for item in listings if item["company"] == "NoDateCo")
    assert nodateco["posted_date"] is None
    assert nodateco["age_label"] == ""


def test_parse_zshah_skips_job_missing_url():
    fixture = {"jobs": [{"company": "BadCo", "title": "Intern", "location": "Remote",
                          "url": None, "posted_at": None, "sponsorship": "unknown", "program": "Internship"}]}
    listings = parse_zshah(fixture, _date(2026, 8, 17))
    assert listings == []
