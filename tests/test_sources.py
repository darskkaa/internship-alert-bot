from datetime import date, datetime, timezone

from sources import (
    age_from_date,
    classify_category,
    normalize_key,
    utc_today,
)


def test_utc_today_matches_utc_clock_not_ambient_system_timezone():
    # Regression guard for the bug where bare date.today() reads the
    # ambient/system timezone instead of UTC, causing age_label drift of up
    # to a full day depending on what machine/timezone runs the code.
    assert utc_today() == datetime.now(timezone.utc).date()



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
            "skills": ["Python", "SQL"],
            "remote": True,
            "h1b_approvals": 133,
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
        {
            "company": "PaidCo",
            "title": "Backend Intern",
            "location": "Remote",
            "url": "https://job-boards.greenhouse.io/paidco/jobs/5",
            "posted_at": "2026-08-16T00:00:00Z",
            "sponsorship": "unknown",
            "program": "Internship",
            "salary": "$30/hr",
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
    assert snorkel["posted_at"].isoformat() == "2026-08-14T21:35:22+00:00"


def test_parse_zshah_midnight_utc_is_date_only_placeholder():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    acme = next(item for item in listings if item["company"] == "Acme Corp")
    assert acme["posted_date"] == _date(2026, 8, 16)
    assert acme["posted_at"] is None


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


def test_parse_zshah_captures_salary_when_present():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    paidco = next(item for item in listings if item["company"] == "PaidCo")
    assert paidco["salary"] == "$30/hr"


def test_parse_zshah_salary_none_when_absent():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    assert snorkel["salary"] is None


def test_parse_zshah_captures_skills_when_present():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    assert snorkel["skills"] == ["Python", "SQL"]


def test_parse_zshah_skills_empty_list_when_absent():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    nodateco = next(item for item in listings if item["company"] == "NoDateCo")
    assert nodateco["skills"] == []


def test_parse_zshah_captures_remote_flag_true():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    assert snorkel["remote"] is True


def test_parse_zshah_remote_false_when_absent():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    nodateco = next(item for item in listings if item["company"] == "NoDateCo")
    assert nodateco["remote"] is False


def test_parse_zshah_captures_h1b_approvals_when_present():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    snorkel = next(item for item in listings if item["company"] == "Snorkel AI")
    assert snorkel["h1b_approvals"] == 133


def test_parse_zshah_h1b_approvals_none_when_absent():
    listings = parse_zshah(ZSHAH_FIXTURE, _date(2026, 8, 17))
    nodateco = next(item for item in listings if item["company"] == "NoDateCo")
    assert nodateco["h1b_approvals"] is None


def test_parse_zshah_skips_job_missing_url():
    fixture = {"jobs": [{"company": "BadCo", "title": "Intern", "location": "Remote",
                          "url": None, "posted_at": None, "sponsorship": "unknown", "program": "Internship"}]}
    listings = parse_zshah(fixture, _date(2026, 8, 17))
    assert listings == []


def _zshah_job(**overrides):
    job = {
        "company": "TimeCo", "title": "SWE Intern", "url": "https://timeco.example/1",
        "program": "Internship", "sponsorship": "unknown",
    }
    job.update(overrides)
    return {"jobs": [job]}


def test_parse_zshah_trusts_posted_at_source_date_only_even_with_nonmidnight_time():
    data = _zshah_job(posted_at="2026-08-16T13:00:00Z", posted_at_source="date_only")
    item = parse_zshah(data, _date(2026, 8, 17))[0]
    assert item["posted_date"] == _date(2026, 8, 16)
    assert item["posted_at"] is None


def test_parse_zshah_trusts_posted_at_source_exact_even_at_midnight():
    data = _zshah_job(posted_at="2026-08-16T00:00:00Z", posted_at_source="exact")
    item = parse_zshah(data, _date(2026, 8, 17))[0]
    assert item["posted_at"] is not None


def test_parse_zshah_season_dropped_when_not_stated():
    stated = parse_zshah(_zshah_job(season="Fall 2026"), _date(2026, 8, 17))[0]
    unstated = parse_zshah(_zshah_job(season="Not stated"), _date(2026, 8, 17))[0]
    assert stated["season"] == "Fall 2026"
    assert unstated["season"] is None


from sources import parse_simplify


def _simplify_entry(**overrides):
    entry = {
        "company_name": "Acme", "title": "SWE Intern", "url": "https://acme.example/1?utm_source=Simplify&ref=Simplify&gh_jid=42",
        "locations": ["New York, NY", "Remote in USA"], "date_posted": 1786000000, "terms": ["Summer 2027"],
        "active": True, "is_visible": True, "category": "Software", "sponsorship": "Other",
    }
    entry.update(overrides)
    return entry


def test_parse_simplify_maps_core_fields_with_exact_time():
    item = parse_simplify([_simplify_entry()], date(2026, 8, 17))[0]
    assert item["company"] == "Acme"
    assert item["location"] == "New York, NY, Remote in USA"
    assert item["category"] == "Software Engineering"
    assert item["posted_at"] == datetime.fromtimestamp(1786000000, timezone.utc)
    assert item["posted_date"] == item["posted_at"].date()
    assert item["season"] == "Summer 2027"
    assert item["source"] == "Simplify"


def test_parse_simplify_skips_inactive_and_hidden():
    data = [_simplify_entry(active=False), _simplify_entry(is_visible=False, url="https://b.example/2")]
    assert parse_simplify(data, date(2026, 8, 17)) == []


def test_parse_simplify_filters_to_allowed_terms():
    data = [
        _simplify_entry(terms=["Winter 2026"], url="https://a.example/1"),
        _simplify_entry(terms=["N/A"], url="https://a.example/2"),
        _simplify_entry(terms=["Fall 2026", "Winter 2026"], url="https://a.example/3"),
    ]
    items = parse_simplify(data, date(2026, 8, 17))
    assert [i["season"] for i in items] == ["Fall 2026"]


def test_parse_simplify_maps_sponsorship_enum():
    no_sponsor = parse_simplify([_simplify_entry(sponsorship="Does Not Offer Sponsorship")], date(2026, 8, 17))[0]
    citizens = parse_simplify([_simplify_entry(sponsorship="U.S. Citizenship is Required")], date(2026, 8, 17))[0]
    assert no_sponsor["sponsorship_flag"] is True and no_sponsor["citizenship_flag"] is False
    assert citizens["citizenship_flag"] is True and citizens["sponsorship_flag"] is False


def test_parse_simplify_strips_tracking_params_but_keeps_functional_ones():
    item = parse_simplify([_simplify_entry()], date(2026, 8, 17))[0]
    assert item["apply_url"] == "https://acme.example/1?gh_jid=42"


def test_parse_simplify_falls_back_to_title_category_and_handles_missing_date():
    item = parse_simplify([_simplify_entry(category="Mystery", title="Quantitative Trading Intern", date_posted=None)], date(2026, 8, 17))[0]
    assert item["category"] == "Quantitative Finance"
    assert item["posted_date"] is None and item["posted_at"] is None


def test_parse_simplify_skips_entry_missing_url():
    assert parse_simplify([_simplify_entry(url=None)], date(2026, 8, 17)) == []



from sources import job_id_key


def test_job_id_key_matches_greenhouse_job_across_url_shapes():
    assert job_id_key("https://stripe.com/jobs/search?gh_jid=7123") == "job:greenhouse:7123"
    assert job_id_key("https://job-boards.greenhouse.io/stripe/jobs/7123") == "job:greenhouse:7123"


def test_job_id_key_lever_ashby_and_unknown():
    assert job_id_key("https://jobs.lever.co/palantir/ABC-123/apply") == "job:lever:abc-123"
    assert job_id_key("https://jobs.ashbyhq.com/ramp/uuid-1") == "job:ashby:uuid-1"
    assert job_id_key("https://acme.com/careers/1") is None


from sources import is_cyber_role


def test_is_cyber_role_matches_security_titles():
    for title in ("Cybersecurity Analyst Intern", "Offensive Security Intern (Summer 2027)", "SOC Analyst Intern",
                  "Penetration Testing Intern", "Red Team Intern", "AppSec Engineer Intern", "Threat Intelligence Co-op",
                  "Identity and Access Management Intern", "Incident Response Intern", "Cyber Research Internship"):
        assert is_cyber_role(title), title


def test_is_cyber_role_rejects_non_security_titles():
    for title in ("Software Engineer Intern", "Securities Trading Intern", "Summer 2027 Intern, National Security Project",
                  "Software Engineer Intern (Security Clearance Required)", "Social Media Intern", "Soccer Analytics Intern"):
        assert not is_cyber_role(title), title
