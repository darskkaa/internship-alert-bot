# Multi-source tracking + richer embeds — design

## Goal

Add a second listing source, and make each Discord alert carry more usable
information (posted date, freshness, category, and — when the source
provides it — sponsorship/citizenship signals), without adding nuisance
(duplicate pings, dead links, noisy formatting) or changing how the bot is
deployed/scheduled.

## Sources

| Source | Format | Category info | Date info | Extra signals |
|---|---|---|---|---|
| SimplifyJobs/Summer2027-Internships (existing) | HTML `<table>` per `##` section | Section header (SWE / PM / Data / Quant / Hardware) | Relative age (`"3d"`, `"1mo"`) | none |
| vanshb03/Summer2027-Internships (new) | one flat markdown pipe table | none | Absolute date (`"Aug 16"`, no year) | 🛂 no sponsorship, 🇺🇸 citizenship required, 🔒 closed |

Neither source gives both a category and a date in the same shape the other
does — the design fills each gap from the other source's data, or by
inference, rather than leaving fields blank.

## Common listing schema

Every source adapter returns a list of dicts in this shape:

```python
{
    "company": str,
    "role": str,
    "location": str,
    "apply_url": str,
    "category": str,          # "Software Engineering" | "Data Science" | "Product Management"
                               # | "Quantitative Finance" | "Hardware Engineering" | "Other"
    "posted_date": date | None,   # calendar date, inferred if source only gives age
    "age_days": int | None,       # inferred if source only gives a date
    "age_label": str,             # human string: "3d ago" / "~1mo ago" / "today"
    "oa_lc_flag": bool,
    "sponsorship_flag": bool,     # True if source marks "no sponsorship" (vansh only; False elsewhere)
    "citizenship_flag": bool,     # True if source marks "US citizenship required" (vansh only)
    "source": str,                # "Simplify" | "Vansh"
}
```

Listings the source itself marks closed (vansh's 🔒) are dropped by the
adapter — never enter the merged list at all.

## Date/age inference

- SimplifyJobs gives `age_days` (or `"Nmo"`) → `posted_date = today - age_days`
  (months approximated as `N * 30` days, and `age_label` keeps the
  `"~1mo ago"` phrasing rather than implying false day-precision).
- vansh gives `posted_date` (month/day, no year) → assume current year; if
  that lands more than 3 days in the future (i.e. the date is actually from
  last year — matters around a Dec/Jan boundary), roll back one year.
  `age_days = (today - posted_date).days`.
- Embed always shows both: `📅 Aug 14, 2026 · 3d ago`.

This mirrors the existing OA/LC keyword match, which is already documented
in `core.py` as a best-effort heuristic, not authoritative — same spirit
applies here: inferred dates are a best-effort fill, not a guarantee.

## Categorization for the flat-table source

vansh has no section headers, so its listings get a category via keyword
match on the role title, reusing the same 5 category buckets SimplifyJobs
already has (SWE / PM / Data Science / Quant / Hardware, default `"Other"`
if nothing matches). Same trade-off the existing OA/LC flag already accepts:
best-effort, not authoritative.

## Cross-source dedup

Two dedup layers, both against `state.json`:

1. **Per-URL** (existing behavior, unchanged) — `apply_url` already in
   `state["seen"]` → skip.
2. **Cross-source key** (new) — normalize `f"{company} {role}"` (lowercase,
   strip punctuation/emoji/whitespace) into a key; if that key is already in
   `state["seen_keys"]`, skip, even if the URL is new. This catches the same
   internship appearing on both trackers with different application links.

Both sets are updated together after each run. `state.json` gains a
`"seen_keys"` list alongside the existing `"seen"` list — `load_state()`
defaults it to `[]` when absent (same graceful-migration pattern already
used for the old list-only format), so no manual migration step is needed
and the change is backward compatible with the current file.

## Architecture: source adapters

```python
def fetch_simplify() -> list[dict]: ...   # existing HTML-table parser, extended with category/date/age
def fetch_vansh() -> list[dict]: ...      # new markdown-table parser

SOURCES = [fetch_simplify, fetch_vansh]

def run_once(state: dict) -> dict:
    listings = [item for fetch in SOURCES for item in fetch()]
    # dedup against state["seen"] + state["seen_keys"], post new ones, update both sets
```

Adding a third source later means writing one function with this
return shape and appending it to `SOURCES` — nothing else changes.

## Embed format

Structured Discord embed fields instead of squeezing everything into the
description:

```
Title: {company} — {role}                (links to apply_url)
Fields:
  📍 Location
  🏷️ Category
  📅 Posted   Aug 14, 2026 · 3d ago
  ⚠️ (only if oa_lc_flag) title mentions OA/LeetCode/assessment tooling
  🛂 (only if sponsorship_flag) No sponsorship
  🇺🇸 (only if citizenship_flag) US citizenship required
Footer: via {source}
Color: green (unchanged)
```

Role ping (`DISCORD_ROLE_ID`) behavior is unchanged — pinged once per batch,
not per listing, to avoid nuisance. Each embed also carries Discord's native
`timestamp` field (the source's inferred `posted_date`, ISO date string),
separate from the human-readable "📅 Posted" text field — the field is
scannable at a glance, the native timestamp renders in the viewer's own
timezone. Omitted when `posted_date` is `None`.

New listings within a run are posted newest-first (sorted by `posted_date`,
descending; dateless listings sort last) rather than in whatever order the
sources happened to return them.

## Tracking-parameter stripping

Apply URLs from both sources carry analytics query params (`utm_source`,
`utm_medium`, `ref`, etc.) that serve the source site's own analytics, not
the applicant. These are stripped from `apply_url` before it's stored in a
listing dict — link destination and any functional query params (job IDs,
requisition numbers) are preserved; only a fixed, conservative blocklist of
known-analytics-only param names/prefixes is removed.

## Error handling

- If one source's fetch/parse raises, log and continue with the other
  source's listings rather than failing the whole run (a markdown format
  change on vansh's side shouldn't silence Simplify alerts, or vice versa).
  `watch.py`'s existing top-level try/except still catches anything that
  escapes both.
- `run_once.py` (used by GitHub Actions) keeps its current behavior of
  letting exceptions propagate after both sources are attempted, so Actions
  still surfaces a failed run if something is seriously wrong.

## Testing

- Unit-style manual check (matches existing project's no-test-suite
  convention): a `--dry-run` pass that fetches both sources, prints the
  merged/deduped list with computed category + age, without posting.
- Verify against the already-known count: current `state.json` has 426
  Simplify listings seeded; after the change, first run should show that
  count plus however many net-new (post-dedup) vansh listings, and post
  nothing (both sources' current listings get seeded into `seen`/`seen_keys`
  same as the existing first-run behavior).
- One live dry run against production data before enabling posting, same
  verification approach used for the earlier 3-day backfill.

## Out of scope (explicitly deferred, not silently dropped)

- LLM-based relevance scoring / enrichment — user chose not to pursue this
  now; the schema above doesn't preclude adding it later.
- Google Search freshness checking of application links — same, deferred.
- Fuzzy (similarity-score) dedup — normalized exact match chosen instead,
  no new dependency.
