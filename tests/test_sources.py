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
