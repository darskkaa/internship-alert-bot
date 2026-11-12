import os

os.environ.setdefault("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test/test")


import pytest


@pytest.fixture(autouse=True)
def _isolated_queue_file(tmp_path, monkeypatch):
    import core

    monkeypatch.setattr(core, "QUEUE_FILE", tmp_path / "queue.json")
