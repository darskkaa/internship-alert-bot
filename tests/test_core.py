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


def test_run_once_posts_new_listings_oldest_first_within_freshness_window(monkeypatch):
    from datetime import date as _date
    from datetime import timedelta as _timedelta

    today = _date.today()

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
    assert embed["footer"]["text"] == "via Vansh"


def test_build_embed_includes_native_discord_timestamp():
    embed = build_embed(_item(posted_date=date(2026, 8, 14)))
    # Noon UTC, not midnight - midnight rolls back to "yesterday" once
    # Discord converts it to a negative-UTC-offset viewer's local clock.
    assert embed["timestamp"] == "2026-08-14T12:00:00+00:00"


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


def test_build_embed_omits_timestamp_when_posted_date_missing():
    embed = build_embed(_item(posted_date=None))
    assert "timestamp" not in embed
