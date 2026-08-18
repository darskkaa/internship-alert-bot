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
