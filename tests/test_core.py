import json

import pytest

import core

TEST_FEED = "seeded-test-feed"


@pytest.fixture(autouse=True)
def _treat_test_feed_as_already_seeded(monkeypatch):
    monkeypatch.setattr(core, "LEGACY_FEEDS", [TEST_FEED])


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
        "feed": TEST_FEED,
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


def test_run_once_posts_new_listings_oldest_first_within_freshness_window(monkeypatch):
    from datetime import timedelta as _timedelta

    from sources import utc_today

    today = utc_today()

    older = _fake_listing("OldCo", "SWE Intern", "https://oldco.example/1")
    older["posted_date"] = today - _timedelta(days=6)
    newer = _fake_listing("NewCo", "SWE Intern", "https://newco.example/1")
    newer["posted_date"] = today - _timedelta(days=1)
    stale = _fake_listing("StaleCo", "SWE Intern", "https://staleco.example/1")
    stale["posted_date"] = today - _timedelta(days=8)  # outside the 7-day window
    dateless = _fake_listing("NoDateCo", "SWE Intern", "https://nodateco.example/1")
    dateless["posted_date"] = None

    # Sources return them out of order on purpose, to prove run_once sorts.
    monkeypatch.setattr(core, "SOURCES", [lambda: [older, dateless, newer, stale]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    # Oldest (and dateless, treated as oldest) posted first so the freshest
    # listing lands in the last-sent Discord message - the one visible at
    # the bottom of the channel. Stale (>7d) is excluded entirely.
    assert [item["company"] for item in posted] == ["NoDateCo", "OldCo", "NewCo"]
    # Stale listing is still marked seen so it's never retried, even though
    # it wasn't posted.
    assert "https://staleco.example/1" in state["seen"]


def test_run_once_dedupes_same_role_appearing_on_two_sources_same_cycle(monkeypatch):
    simplify_item = _fake_listing("Acme", "SWE Intern", "https://simplify.example/acme", source="Simplify")
    vansh_item = _fake_listing("Acme", "SWE Intern", "https://vansh.example/acme", source="Vansh")
    monkeypatch.setattr(core, "SOURCES", [lambda: [simplify_item], lambda: [vansh_item]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    # Neither URL nor the key has been seen before — both are "first appearance"
    # this cycle, on two different sources simultaneously.
    core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert len(posted) == 1


def test_run_once_isolates_source_returning_malformed_listing(monkeypatch):
    malformed = {"company": "BadCo"}  # missing apply_url, role, posted_date
    good_listing = _fake_listing("Acme", "SWE Intern", "https://acme.example/1")
    monkeypatch.setattr(core, "SOURCES", [lambda: [malformed], lambda: [good_listing]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert posted == [good_listing]


def test_run_once_drops_only_malformed_item_not_whole_source(monkeypatch):
    malformed = {"company": "BadCo"}  # missing everything else
    good = _fake_listing("Acme", "SWE Intern", "https://acme.example/1")
    monkeypatch.setattr(core, "SOURCES", [lambda: [malformed, good]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert posted == [good]


def test_run_once_excludes_previously_seen_url_even_with_empty_seen_keys(monkeypatch):
    listing = _fake_listing("Acme", "SWE Intern", "https://acme.example/already-seen")
    monkeypatch.setattr(core, "SOURCES", [lambda: [listing]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://acme.example/already-seen"], "seen_keys": [], "last_checked_utc": "x"})

    assert posted == []


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
    assert embed["footer"]["text"] == "via Vansh · detected"


def test_build_embed_timestamp_is_detection_time():
    from datetime import datetime, timezone

    detected = datetime(2026, 8, 17, 15, 30, tzinfo=timezone.utc)
    embed = build_embed(_item(posted_date=date(2026, 8, 14)), detected)
    assert embed["timestamp"] == "2026-08-17T15:30:00+00:00"


def test_build_embed_posted_field_uses_discord_markup_when_real_time_known():
    from datetime import datetime, timezone

    posted_at = datetime(2026, 8, 14, 21, 35, 22, tzinfo=timezone.utc)
    embed = build_embed(_item(posted_at=posted_at))
    posted_field = next(f for f in embed["fields"] if f["name"] == "📅 Posted")
    ts = int(posted_at.timestamp())
    assert posted_field["value"] == f"<t:{ts}:f> (<t:{ts}:R>)"


def test_run_once_sorts_same_day_listings_by_real_posted_time(monkeypatch):
    from datetime import datetime, timezone

    from sources import utc_today

    today = utc_today()
    late = _fake_listing("LateCo", "SWE Intern", "https://lateco.example/1")
    late["posted_date"] = today
    late["posted_at"] = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=20)
    early = _fake_listing("EarlyCo", "SWE Intern", "https://earlyco.example/1")
    early["posted_date"] = today
    early["posted_at"] = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).replace(hour=3)
    monkeypatch.setattr(core, "SOURCES", [lambda: [late, early]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert [item["company"] for item in posted] == ["EarlyCo", "LateCo"]


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


def test_build_embed_still_stamps_detection_time_when_posted_date_missing():
    embed = build_embed(_item(posted_date=None))
    assert "timestamp" in embed
    assert not any(f["name"] == "📅 Posted" for f in embed["fields"])


def test_build_embed_tags_non_internship_program():
    embed = build_embed(_item(program="Co-op"))
    program_field = next(f for f in embed["fields"] if f["name"] == "🔁 Program")
    assert program_field["value"] == "Co-op"


def test_build_embed_omits_program_tag_for_plain_internship():
    embed = build_embed(_item(program="Internship"))
    assert not any(f["name"] == "🔁 Program" for f in embed["fields"])


def test_build_embed_omits_program_tag_when_field_absent():
    embed = build_embed(_item())
    assert not any(f["name"] == "🔁 Program" for f in embed["fields"])


def test_build_embed_shows_pay_when_present():
    embed = build_embed(_item(salary="$30/hr"))
    pay_field = next(f for f in embed["fields"] if f["name"] == "💰 Pay")
    assert pay_field["value"] == "$30/hr"


def test_build_embed_omits_pay_when_absent():
    embed = build_embed(_item())
    assert not any(f["name"] == "💰 Pay" for f in embed["fields"])


def test_build_embed_omits_pay_when_none():
    embed = build_embed(_item(salary=None))
    assert not any(f["name"] == "💰 Pay" for f in embed["fields"])


def test_build_embed_shows_skills_when_present():
    embed = build_embed(_item(skills=["Python", "SQL"]))
    skills_field = next(f for f in embed["fields"] if f["name"] == "🛠️ Skills")
    assert skills_field["value"] == "Python, SQL"


def test_build_embed_omits_skills_when_absent():
    embed = build_embed(_item())
    assert not any(f["name"] == "🛠️ Skills" for f in embed["fields"])


def test_build_embed_omits_skills_when_empty_list():
    embed = build_embed(_item(skills=[]))
    assert not any(f["name"] == "🛠️ Skills" for f in embed["fields"])


def test_build_embed_caps_skills_list_and_shows_remainder_count():
    embed = build_embed(_item(skills=[f"Skill{i}" for i in range(9)]))
    skills_field = next(f for f in embed["fields"] if f["name"] == "🛠️ Skills")
    assert skills_field["value"] == "Skill0, Skill1, Skill2, Skill3, Skill4, Skill5 +3 more"


def test_build_embed_skills_value_truncated_when_overlong():
    embed = build_embed(_item(skills=["X" * 2000]))
    skills_field = next(f for f in embed["fields"] if f["name"] == "🛠️ Skills")
    assert len(skills_field["value"]) == 1024
    assert skills_field["value"].endswith("…")


def test_build_embed_shows_remote_badge_when_true():
    embed = build_embed(_item(remote=True))
    remote_field = next(f for f in embed["fields"] if f["name"] == "🌐 Remote")
    assert remote_field["value"] == "Remote-eligible"


def test_build_embed_omits_remote_badge_when_false():
    embed = build_embed(_item(remote=False))
    assert not any(f["name"] == "🌐 Remote" for f in embed["fields"])


def test_build_embed_omits_remote_badge_when_absent():
    embed = build_embed(_item())
    assert not any(f["name"] == "🌐 Remote" for f in embed["fields"])


def test_build_embed_shows_h1b_history_when_present():
    embed = build_embed(_item(h1b_approvals=133))
    h1b_field = next(f for f in embed["fields"] if f["name"] == "📊 Employer H-1B history")
    assert "133" in h1b_field["value"]
    assert "company-wide" in h1b_field["value"]
    assert "not role-specific" in h1b_field["value"]


def test_build_embed_omits_h1b_history_when_absent():
    embed = build_embed(_item())
    assert not any(f["name"] == "📊 Employer H-1B history" for f in embed["fields"])


def test_build_embed_omits_h1b_history_when_none():
    embed = build_embed(_item(h1b_approvals=None))
    assert not any(f["name"] == "📊 Employer H-1B history" for f in embed["fields"])


def test_build_embed_omits_h1b_history_when_zero():
    embed = build_embed(_item(h1b_approvals=0))
    assert not any(f["name"] == "📊 Employer H-1B history" for f in embed["fields"])


def test_build_embed_h1b_history_uses_thousands_separator():
    embed = build_embed(_item(h1b_approvals=1234))
    h1b_field = next(f for f in embed["fields"] if f["name"] == "📊 Employer H-1B history")
    assert "1,234" in h1b_field["value"]


def test_save_state_skips_write_when_nothing_changed(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(core, "STATE_FILE", state_file)
    state = {"seen": ["x"], "seen_keys": ["k"], "last_checked_utc": None}

    core.save_state(state)
    first = state_file.read_text()
    core.save_state({"seen": ["x"], "seen_keys": ["k"], "last_checked_utc": None})

    assert state_file.read_text() == first


def test_save_state_writes_when_seen_changes(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(core, "STATE_FILE", state_file)
    core.save_state({"seen": ["x"], "seen_keys": ["k"]})

    core.save_state({"seen": ["x", "y"], "seen_keys": ["k"]})

    assert json.loads(state_file.read_text())["seen"] == ["x", "y"]


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {}


def test_batch_embeds_splits_on_char_budget():
    big = {"title": "t", "footer": {"text": "f"}, "fields": [{"name": "n", "value": "v" * 2000}]}
    batches = core._batch_embeds([big, big, big, big])
    assert [len(b) for b in batches] == [2, 2]


def test_batch_embeds_caps_at_ten():
    small = {"title": "t", "footer": {"text": "f"}, "fields": []}
    batches = core._batch_embeds([small] * 23)
    assert [len(b) for b in batches] == [10, 10, 3]


def test_post_new_listings_falls_back_to_singles_on_400(monkeypatch):
    sent = []

    def fake_send(payload):
        sent.append(payload)
        bad = any(e["title"].startswith("Bad") for e in payload["embeds"])
        return _Resp(400 if bad else 204)

    monkeypatch.setattr(core, "_send", fake_send)
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    items = [_item(company="Good"), _item(company="Bad")]

    core.post_new_listings(items)

    assert [len(p["embeds"]) for p in sent] == [2, 1, 1]


def test_run_once_notifies_after_repeated_source_failure(monkeypatch):
    def broken():
        raise RuntimeError("down")

    notices = []
    monkeypatch.setattr(core, "SOURCES", [broken])
    monkeypatch.setattr(core, "_notify", notices.append)
    state = {"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"}

    for _ in range(core.FAILURE_ALERT_THRESHOLD + 2):
        state = core.run_once(state)

    assert len(notices) == 1
    assert state["source_failures"]["broken"] == core.FAILURE_ALERT_THRESHOLD + 2


def test_run_once_treats_empty_source_as_unhealthy_and_recovers(monkeypatch):
    results = {"value": []}

    def flaky():
        return results["value"]

    monkeypatch.setattr(core, "SOURCES", [flaky])
    monkeypatch.setattr(core, "_notify", lambda text: None)
    state = {"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"}

    state = core.run_once(state)
    assert state["source_failures"] == {"flaky": 1}

    results["value"] = [_fake_listing("Acme", "SWE Intern", "https://acme.example/1")]
    monkeypatch.setattr(core, "post_new_listings", lambda items: None)
    state = core.run_once(state)
    assert state["source_failures"] == {}


def test_build_embed_shows_season_only_when_present():
    shown = build_embed(_item(season="Summer 2027"))
    assert any(f["name"] == "🗓️ Season" and f["value"] == "Summer 2027" for f in shown["fields"])
    hidden = build_embed(_item(season=None))
    assert not any(f["name"] == "🗓️ Season" for f in hidden["fields"])


def test_run_once_silently_seeds_a_feed_seen_for_the_first_time(monkeypatch):
    from sources import utc_today

    legacy = _fake_listing("OldCo", "SWE Intern", "https://oldco.example/1")
    legacy["posted_date"] = utc_today()
    from datetime import timedelta

    fresh_board = _fake_listing("BoardCo", "SWE Intern", "https://boardco.example/1")
    fresh_board["posted_date"] = utc_today() - timedelta(days=3)
    fresh_board["feed"] = "greenhouse:boardco"
    monkeypatch.setattr(core, "SOURCES", [lambda: [legacy, fresh_board]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert [item["company"] for item in posted] == ["OldCo"]
    assert "https://boardco.example/1" in state["seen"]
    assert "greenhouse:boardco" in state["seeded_feeds"]


def test_run_once_alerts_on_a_seeded_feeds_next_new_listing(monkeypatch):
    from sources import utc_today

    item = _fake_listing("BoardCo", "SWE Intern", "https://boardco.example/2")
    item["posted_date"] = utc_today()
    item["feed"] = "greenhouse:boardco"
    monkeypatch.setattr(core, "SOURCES", [lambda: [item]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "seeded_feeds": ["greenhouse:boardco"], "last_checked_utc": "x"})

    assert [i["company"] for i in posted] == ["BoardCo"]


def test_run_once_seeds_quiet_feed_so_its_first_role_alerts_later(monkeypatch):
    from sources import utc_today

    def quiet_source():
        return []

    quiet_source.fetched_feeds = {"greenhouse:quietco"}
    other = _fake_listing("Acme", "SWE Intern", "https://acme.example/9")
    monkeypatch.setattr(core, "SOURCES", [quiet_source, lambda: [other]])
    monkeypatch.setattr(core, "_notify", lambda text: None)
    monkeypatch.setattr(core, "post_new_listings", lambda items: None)

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})
    assert "greenhouse:quietco" in state["seeded_feeds"]

    first_role = _fake_listing("QuietCo", "SWE Intern", "https://quietco.example/1")
    first_role["posted_date"] = utc_today()
    first_role["feed"] = "greenhouse:quietco"
    posted = []
    monkeypatch.setattr(core, "SOURCES", [lambda: [first_role]])
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    core.run_once(state)

    assert [i["company"] for i in posted] == ["QuietCo"]


def test_run_once_first_sight_feed_still_alerts_roles_posted_in_last_day(monkeypatch):
    from datetime import datetime, timedelta, timezone

    just_posted = _fake_listing("BoardCo", "SWE Intern", "https://boardco.example/3")
    just_posted["posted_date"] = None
    just_posted["posted_at"] = datetime.now(timezone.utc) - timedelta(hours=2)
    just_posted["feed"] = "greenhouse:boardco"
    dateless = _fake_listing("BoardCo", "PM Intern", "https://boardco.example/4")
    dateless["posted_date"] = None
    dateless["feed"] = "greenhouse:boardco"
    monkeypatch.setattr(core, "SOURCES", [lambda: [just_posted, dateless]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert [i["role"] for i in posted] == ["SWE Intern"]


def test_run_once_dedupes_same_ats_job_across_company_spellings(monkeypatch):
    from sources import utc_today

    simplify = _fake_listing("Anduril Industries", "Software Engineer Intern", "https://job-boards.greenhouse.io/andurilindustries/jobs/4242")
    board = _fake_listing("Anduril", "Software Engineer Intern", "https://www.anduril.com/careers?gh_jid=4242")
    for item in (simplify, board):
        item["posted_date"] = utc_today()
    monkeypatch.setattr(core, "SOURCES", [lambda: [simplify], lambda: [board]])
    posted = []
    monkeypatch.setattr(core, "post_new_listings", lambda items: posted.extend(items))

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert len(posted) == 1
    assert "job:greenhouse:4242" in state["seen_keys"]


def test_post_new_listings_skips_single_rejected_embed_instead_of_raising(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "_send", lambda payload: sent.append(payload) or _Resp(400))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    core.post_new_listings([_item(company="Bad")])

    assert len(sent) == 1


def test_run_once_board_source_with_no_open_roles_is_healthy(monkeypatch):
    def quiet_boards():
        return []

    quiet_boards.fetched_feeds = {"greenhouse:quietco"}
    monkeypatch.setattr(core, "SOURCES", [quiet_boards])
    monkeypatch.setattr(core, "post_new_listings", lambda items: None)

    state = core.run_once({"seen": ["https://seed"], "seen_keys": ["seed key"], "last_checked_utc": "x"})

    assert state["source_failures"] == {}
