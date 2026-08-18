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
