from datetime import date, datetime, timezone

import ats

TODAY = date(2026, 10, 1)
BOARD = {"name": "Acme", "ats": "greenhouse", "token": "acme"}


def test_greenhouse_keeps_interns_uses_first_published_and_drops_others():
    data = {"jobs": [
        {"title": "Software Engineer Intern", "absolute_url": "https://boards.example/acme/1",
         "location": {"name": "NYC"}, "first_published": "2026-09-30T14:03:01-04:00", "updated_at": "2026-10-01T00:00:00Z"},
        {"title": "Senior Engineer", "absolute_url": "https://boards.example/acme/2", "location": {"name": "NYC"}},
        {"title": "International Sales Lead", "absolute_url": "https://boards.example/acme/3", "location": {"name": "NYC"}},
    ]}
    items = ats.parse_greenhouse(data, BOARD, TODAY)
    assert [i["role"] for i in items] == ["Software Engineer Intern"]
    item = items[0]
    assert item["company"] == "Acme" and item["location"] == "NYC"
    assert item["posted_at"] == datetime(2026, 9, 30, 18, 3, 1, tzinfo=timezone.utc)
    assert item["feed"] == "greenhouse:acme" and item["source"] == "Greenhouse"


def test_season_in_title_filters_to_allowed_seasons():
    data = {"jobs": [
        {"title": "SWE Intern - Summer 2027", "absolute_url": "https://b.example/1"},
        {"title": "SWE Intern - Summer 2026", "absolute_url": "https://b.example/2"},
        {"title": "SWE Intern", "absolute_url": "https://b.example/3"},
    ]}
    items = ats.parse_greenhouse(data, BOARD, TODAY)
    assert [(i["apply_url"], i["season"]) for i in items] == [
        ("https://b.example/1", "Summer 2027"), ("https://b.example/3", None),
    ]


def test_lever_uses_created_at_millis_and_remote_workplace():
    board = {"name": "Spot", "ats": "lever", "token": "spot"}
    data = [{"text": "Data Science Intern", "hostedUrl": "https://jobs.example/spot/1",
             "createdAt": 1790000000000, "categories": {"location": "Remote"}, "workplaceType": "remote"}]
    item = ats.parse_lever(data, board, TODAY)[0]
    assert item["posted_at"] == datetime.fromtimestamp(1790000000, timezone.utc)
    assert item["remote"] is True and item["location"] == "Remote"
    assert item["category"] == "Data Science"


def test_ashby_skips_unlisted_and_trusts_intern_employment_type():
    board = {"name": "Ramp", "ats": "ashby", "token": "ramp"}
    data = {"jobs": [
        {"title": "Product Analyst", "employmentType": "Intern", "jobUrl": "https://ashby.example/1",
         "publishedAt": "2026-09-30T12:00:00.000+00:00", "isListed": True, "location": "NYC"},
        {"title": "Backend Intern", "jobUrl": "https://ashby.example/2", "isListed": False},
        {"title": "Staff Engineer", "employmentType": "FullTime", "jobUrl": "https://ashby.example/3", "isListed": True},
    ]}
    items = ats.parse_ashby(data, board, TODAY)
    assert [i["role"] for i in items] == ["Product Analyst"]
    assert items[0]["posted_date"] == date(2026, 9, 30)


def test_fetch_ats_isolates_failing_board_but_raises_when_all_fail(monkeypatch):
    boards = [{"name": "A", "ats": "greenhouse", "token": "a"}, {"name": "B", "ats": "greenhouse", "token": "b"}]
    monkeypatch.setattr(ats, "load_watchlist", lambda: boards)

    def fetch(board, today):
        if board["token"] == "a":
            raise RuntimeError("404")
        return [{"company": "B"}]

    monkeypatch.setattr(ats, "fetch_board", fetch)
    assert ats.fetch_ats() == [{"company": "B"}]

    monkeypatch.setattr(ats, "fetch_board", lambda board, today: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        ats.fetch_ats()
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError when every board fails")


def test_watchlist_file_only_lists_supported_boards():
    boards = ats.load_watchlist()
    assert boards and all(b["ats"] in ats.PARSERS and b["token"] and b["name"] for b in boards)


def test_fetch_ats_reports_every_successfully_fetched_board_even_with_zero_roles(monkeypatch):
    boards = [{"name": "A", "ats": "greenhouse", "token": "a"}, {"name": "B", "ats": "lever", "token": "b"}, {"name": "C", "ats": "ashby", "token": "c"}]
    monkeypatch.setattr(ats, "load_watchlist", lambda: boards)

    def fetch(board, today):
        if board["token"] == "c":
            raise RuntimeError("500")
        return [{"company": "A"}] if board["token"] == "a" else []

    monkeypatch.setattr(ats, "fetch_board", fetch)
    ats.fetch_ats()

    assert ats.fetch_ats.fetched_feeds == {"greenhouse:a", "lever:b"}


def test_title_naming_unwanted_season_first_and_allowed_second_is_kept():
    data = {"jobs": [{"title": "SWE Intern - Spring 2027 / Summer 2027", "absolute_url": "https://b.example/9"}]}
    items = ats.parse_greenhouse(data, BOARD, TODAY)
    assert [i["season"] for i in items] == ["Summer 2027"]


def test_watchlist_names_are_clean():
    for board in ats.load_watchlist():
        name = board["name"]
        assert name == name.strip() and "&amp;" not in name and name.lower() != "internship"
