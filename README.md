# internship-alert-bot

Polls internship listing feeds and company job boards, diffs them against
saved state, and posts new listings to a Discord channel via webhook.

Two ways to run it — pick one:

## Option A: GitHub Actions (no server, free)

Runs on GitHub's infra. Nothing to host, keep on, or patch. GitHub's own `schedule`
cron is throttled on free repos (runs can be hours apart), so `cf-trigger/` is a
free Cloudflare Worker cron that dispatches the workflow every 10 minutes:

```
cd cf-trigger
wrangler secret put GITHUB_TOKEN   # fine-grained PAT, this repo only, Actions: Read and write
wrangler deploy
```

Needs a workers.dev subdomain on the Cloudflare account (open Workers & Pages
in the dashboard once). `wrangler tail` shows each tick's result.

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

Seeds `state.json` with every listing currently in the feeds and posts
nothing — only listings added *after* that count as "new." The same applies
to any feed seen for the first time later (a new source, or a new board added
to `watchlist.json`): its existing backlog is marked seen without alerting.

## Sources

- [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) `listings.json` — exact posting time, active/closed flag, term, category, and sponsorship. Only active Summer 2027 / Fall 2026 roles alert.
- [zshah101's tracker](https://github.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships) `jobs.json` — adds season, pay, skills, remote, and employer H-1B history.
- Company job boards (`ats.py`) — polls Greenhouse, Lever, and Ashby public APIs directly for the companies in `watchlist.json`, so roles show up minutes after posting. Add a company with `{"name": "Acme", "ats": "greenhouse", "token": "acme"}` (the token is the slug in the company's board URL).

The same internship on several feeds is only alerted once — matched by
normalized company + role text, not just the application link.

Each alert shows: location, category, posted time (live-relative when the
source gives an exact time, otherwise date + age), season when stated, and any
sponsorship, citizenship, or OA/LeetCode flags the data supports. The embed
timestamp is when the bot detected the listing.

If a source errors or returns nothing 3 runs in a row, the bot posts a notice
to the channel.

## Running tests

```
pip install -r requirements-dev.txt
pytest
```

## The OA/LeetCode flag

Each embed gets a ⚠️ note if the company/role text contains "leetcode",
"online assessment", "hackerrank", "codesignal", or "coderpad". This is a
best-effort hint, not a real filter — the feeds have no field for
interview process at all, and job titles almost never name their assessment
tooling, so this will rarely fire. A real "no OA/no LC" filter would need a
different, hand-maintained data source.

## Config (.env / Actions secrets)

- `DISCORD_WEBHOOK_URL` — required
- `DISCORD_ROLE_ID` — optional
- `GITHUB_REPO` / `GITHUB_BRANCH` — swap the Simplify-format tracker repo
- `POLL_INTERVAL_SECONDS` — Option B only, default 600
