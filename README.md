# internship-alert-bot

Polls the SimplifyJobs/Summer2027-Internships README, diffs it against saved
state, and posts new listings to a Discord channel via webhook.

Two ways to run it — pick one:

## Option A: GitHub Actions (no server, free)

Runs on GitHub's infra every 15 minutes. Nothing to host, keep on, or patch.

1. Repo → Settings → Secrets and variables → Actions → New repository secret
   - `DISCORD_WEBHOOK_URL` (required)
   - `DISCORD_ROLE_ID` (optional, pings a role on new listings)
2. That's it — `.github/workflows/watch.yml` handles the schedule, runs
   `run_once.py`, and commits the updated `state.json` back to the repo each
   time (this also keeps the schedule from auto-disabling after 60 days of
   inactivity, since every run makes a commit).
3. To trigger a run immediately instead of waiting: Actions tab → watch-internships → Run workflow.

## Option B: Always-on machine (Pi, VPS, etc.)

```
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: paste DISCORD_WEBHOOK_URL, optionally DISCORD_ROLE_ID
venv/bin/python watch.py   # loops forever, polls every POLL_INTERVAL_SECONDS
```

Install as a systemd service so it survives reboots/crashes:

```
sudo cp internship-alert-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now internship-alert-bot
journalctl -u internship-alert-bot -f
```

## First run

Seeds `state.json` with every listing currently in the README and posts
nothing — only listings added *after* that count as "new."

## Sources

Tracks two listing repos and merges/dedupes them:

- [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) — category and posting age come directly from this source.
- [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships) — posting date comes directly from this source; category is inferred from the role title. Closed listings (🔒 in the source) are never posted. Sponsorship (🛂) and citizenship (🇺🇸) flags, when present, are shown on the alert.

The same internship posted on both trackers is only alerted once — matched by
normalized company + role text, not just the application link (the two
trackers often link to different application portals for the same job).

Each alert shows: location, category, posted date + freshness ("3d ago"),
and any OA/LeetCode, sponsorship, or citizenship flags the source data
supports.

## Running tests

```
pip install -r requirements-dev.txt
pytest
```

## The OA/LeetCode flag

Each embed gets a ⚠️ note if the company/role text contains "leetcode",
"online assessment", "hackerrank", "codesignal", or "coderpad". This is a
best-effort hint, not a real filter — the source README has no field for
interview process at all, and job titles almost never name their assessment
tooling, so this will rarely fire. A real "no OA/no LC" filter would need a
different, hand-maintained data source.

## Config (.env / Actions secrets)

- `DISCORD_WEBHOOK_URL` — required
- `DISCORD_ROLE_ID` — optional
- `GITHUB_REPO` / `GITHUB_BRANCH` / `README_PATH` — swap to a different tracker repo
- `POLL_INTERVAL_SECONDS` — Option B only, default 600
