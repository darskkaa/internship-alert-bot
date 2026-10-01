import json
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
    assert boards and all((b["ats"] in ats.PARSERS or b["ats"] in ats.CUSTOM_FETCHERS) and b["token"] and b["name"] for b in boards)
    assert all("|" in b["token"] for b in boards if b["ats"] in ats.CUSTOM_FETCHERS)


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


def test_workday_posted_on_parses_relative_days():
    assert ats.workday_posted_on("Posted Today", TODAY) == TODAY
    assert ats.workday_posted_on("Posted Yesterday", TODAY) == date(2026, 9, 30)
    assert ats.workday_posted_on("Posted 3 Days Ago", TODAY) == date(2026, 9, 28)
    assert ats.workday_posted_on("Posted 30+ Days Ago", TODAY) == date(2026, 8, 31)
    assert ats.workday_posted_on(None, TODAY) is None


def test_parse_workday_builds_job_url_and_date_only_posting():
    board = {"name": "RTX", "ats": "workday", "token": "globalhr.wd5.myworkdayjobs.com|REC_RTX_Ext_Gateway"}
    postings = [
        {"title": "Summer 2027 Cyber Intern - Onsite", "externalPath": "/job/US-MA/Summer-2027-Cyber-Intern_01879253",
         "postedOn": "Posted Today", "locationsText": "US-MA-CAMBRIDGE"},
        {"title": "Senior Engineer", "externalPath": "/job/US-MA/Senior_1", "postedOn": "Posted Today"},
    ]
    items = ats.parse_workday(postings, board, TODAY)
    assert len(items) == 1
    item = items[0]
    assert item["apply_url"] == "https://globalhr.wd5.myworkdayjobs.com/REC_RTX_Ext_Gateway/job/US-MA/Summer-2027-Cyber-Intern_01879253"
    assert item["posted_date"] == TODAY and item["posted_at"] is None
    assert item["feed"] == "workday:globalhr.wd5.myworkdayjobs.com|REC_RTX_Ext_Gateway"


def test_parse_oracle_reads_requisition_list():
    board = {"name": "Acme Bank", "ats": "oracle", "token": "egug.fa.us2.oraclecloud.com|CX_1"}
    data = {"items": [{"requisitionList": [
        {"Id": "26014649", "Title": "Cybersecurity Intern", "PostedDate": "2026-09-30", "PrimaryLocation": "Phoenix, AZ"},
        {"Id": "1", "Title": "Senior Analyst", "PostedDate": "2026-09-30"},
    ]}]}
    items = ats.parse_oracle(data, board, TODAY)
    assert [i["apply_url"] for i in items] == ["https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/26014649"]
    assert items[0]["posted_date"] == date(2026, 9, 30)


def test_parse_smartrecruiters_uses_released_date():
    board = {"name": "Bosch", "ats": "smartrecruiters", "token": "BoschGroup"}
    data = {"content": [{"id": "744000152821659", "name": "Security Engineering Intern", "releasedDate": "2026-10-01T02:56:36.840Z",
                         "location": {"fullLocation": "Pittsburgh, PA", "remote": False}}]}
    item = ats.parse_smartrecruiters(data, board, TODAY)[0]
    assert item["apply_url"] == "https://jobs.smartrecruiters.com/BoschGroup/744000152821659"
    assert item["posted_at"] == datetime(2026, 10, 1, 2, 56, 36, 840000, tzinfo=timezone.utc)


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self.content = json.dumps(payload).encode()

    def raise_for_status(self):
        pass


def test_fetch_workday_pages_until_a_page_has_no_intern_titles(monkeypatch):
    def page(n_intern, n_other):
        return {"jobPostings": [{"title": f"SWE Intern {i}", "externalPath": f"/job/x/i{i}", "postedOn": "Posted Today"} for i in range(n_intern)]
                + [{"title": f"Senior Engineer {i}", "externalPath": f"/job/x/s{i}", "postedOn": "Posted Today"} for i in range(n_other)]}

    pages = [page(20, 0), page(5, 15), page(0, 20), page(20, 0)]
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json["offset"])
        return _FakeResp(pages[json["offset"] // 20])

    monkeypatch.setattr(ats.SESSION, "post", fake_post)
    board = {"name": "BAH", "ats": "workday", "token": "bah.wd1.myworkdayjobs.com|BAH_Jobs"}
    items = ats.fetch_workday(board, TODAY)

    assert calls == [0, 20, 40]
    assert len(items) == 25


def test_board_tech_filter_keeps_quant_and_hardware_titles_but_not_marketing():
    data = {"jobs": [
        {"title": "2027 Risk Analyst (DMFI) Intern", "absolute_url": "https://b.example/1"},
        {"title": "PD Intern - Physical Design", "absolute_url": "https://b.example/2"},
        {"title": "Performance Marketing Intern", "absolute_url": "https://b.example/3"},
        {"title": "Trading Intern", "absolute_url": "https://b.example/4"},
    ]}
    assert [i["apply_url"][-1] for i in ats.parse_greenhouse(data, BOARD, TODAY)] == ["1", "2", "4"]


def test_enrich_workday_replaces_relative_date_with_exact_start_date(monkeypatch):
    captured = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return _FakeResp({"jobPostingInfo": {"startDate": "2026-09-12", "location": "Herndon, VA", "additionalLocations": ["A", "B"]}})

    monkeypatch.setattr(ats.SESSION, "get", fake_get)
    item = {"apply_url": "https://bah.wd1.myworkdayjobs.com/BAH_Jobs/job/Herndon/Cyber-Intern_R1", "feed": "workday:bah.wd1.myworkdayjobs.com|BAH_Jobs",
            "posted_date": TODAY, "age_days": 0, "age_label": "today", "location": "3 Locations"}

    ats.enrich_listings([item])

    assert captured["url"] == "https://bah.wd1.myworkdayjobs.com/wday/cxs/bah/BAH_Jobs/job/Herndon/Cyber-Intern_R1"
    assert item["posted_date"] == date(2026, 9, 12)
    assert item["location"] == "Herndon, VA +2 more"


def test_enrich_listings_keeps_item_when_detail_fails(monkeypatch):
    def boom(url, timeout=None):
        raise RuntimeError("timeout")

    monkeypatch.setattr(ats.SESSION, "get", boom)
    item = {"apply_url": "https://x.wd1.myworkdayjobs.com/S/job/a/b_1", "feed": "workday:x.wd1.myworkdayjobs.com|S",
            "posted_date": TODAY, "location": "Austin, TX"}
    ats.enrich_listings([item])
    assert item["posted_date"] == TODAY


def test_fetch_workday_keeps_earlier_pages_when_a_deep_page_errors(monkeypatch):
    class Resp(_FakeResp):
        def __init__(self, payload, status=200):
            super().__init__(payload)
            self.status_code = status

        def raise_for_status(self):
            if self.status_code != 200:
                raise RuntimeError(self.status_code)

    first = {"jobPostings": [{"title": f"SWE Intern {i}", "externalPath": f"/job/x/i{i}", "postedOn": "Posted Today"} for i in range(20)]}

    def fake_post(url, json=None, timeout=None):
        return Resp(first) if json["offset"] == 0 else Resp({}, status=503)

    monkeypatch.setattr(ats.SESSION, "post", fake_post)
    monkeypatch.setattr(ats.time, "sleep", lambda s: None)
    items = ats.fetch_workday({"name": "D", "ats": "workday", "token": "disney.wd5.myworkdayjobs.com|disneycareer"}, TODAY)
    assert len(items) == 20
