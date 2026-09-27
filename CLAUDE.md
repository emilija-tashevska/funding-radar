# Funding Radar

A daily screener for recently funded companies. It reads funding news, extracts
structured rounds with Claude, keeps them in SQLite, and publishes a page.

**Why it exists.** It is a source of clients for a new consulting practice —
fractional product, pricing and growth leadership, run with a former banker and a
GTM/AI person. A company that has just raised is a company that has just acquired
a budget and a set of problems it has no one senior to solve. Secondary use: ideas
for what to build and where to work next.

**Owner:** emilija-tashevska. **Repo:** https://github.com/emilija-tashevska/funding-radar (public).
**Page:** https://emilija-tashevska.github.io/funding-radar/

## The brief, as decided

These were settled with the owner. Do not quietly change them.

| Decision | Value |
| --- | --- |
| Stages | Seed to Series B |
| Size floor | **None** (owner, 2026-09-27): every in-brief round is included whatever its size. Was 2M. `min_amount` in config still works if a floor is wanted back |
| Regions | UK and Europe in brief; US and elsewhere stored and filterable, off by default |
| Window | 120-day rolling history on the page |
| Out-of-brief rounds | **Stored and labelled with the reason, never dropped.** The page filters them; the pipeline keeps them |
| Rumoured or in-progress raises | Excluded ("X seeks $500M") — money in the bank only |
| Venture studios | Real rounds, flagged `is_studio`, filterable on the page |
| Rounds whose headline names no company | Fetch the article body once and re-read before dropping |
| Confirmation | Two tiers. Confirmed = amount + stage + (a resolved domain or two independent outlets). Unconfirmed rounds are shown; the owner is fine with that |
| Tracked investors | Antler, Entrepreneur First, Creandum, Techstars (`tracked_investors` in config). The 300k lower floors went with the floor. **Not Y Combinator** — explicitly excluded |
| Enrichment | No outside data. Each in-brief company gets 1-2 sentences on what it does, written from the round's own article (owner, 2026-09-27), with the URL it came from |
| Passphrase | Was in the original brief; the owner later chose a public page with no gate |

## How it works

Six stages, `src/funding_radar/`:

1. **discovery.py** — runs every source in `config/sources.yaml`, dedupes by
   normalised headline, keeps duplicates as corroboration (`Candidate.duplicates`),
   drops anything that is not plausibly a funding round (`looks_like_funding`)
   and anything a VC fund raised for itself (`looks_like_fund_raise`).
2. **extract.py** — Claude Sonnet reads batches of 8 headlines and returns
   structured rounds. **Structured outputs cap an item at 14 properties**, which is
   why fields like round date and lead investor are derived rather than asked for.
   Amounts written in millions are rescaled; a missing currency is read off the
   symbol; region falls back to the country or city.
3. **qualify.py** — normalises the stage, applies the brief, returns a `Verdict`
   with the reason it is out of brief. `approx_usd` is for the floor (1:1 on
   USD/GBP/EUR by choice); `comparable_usd` uses real rates and is only for
   comparing two outlets against each other.
4. **pipeline.py** — stores the round with its evidence, then judges it. Flags are
   computed from the **merged row**, never from the incoming article: a later,
   thinner report must not demote a round we already hold in full.
5. **describe.py** — for each in-brief company without one, reads the article
   (resolving a Google News link to the publisher first, `resolve.py`) and has
   Sonnet write 1-2 sentences from that text only. Tried once per company; a paid
   web-search fallback exists behind `describe.web_search_fallback` (off).
6. **site.py + templates/index.html** — one self-contained page with the data
   inlined. Also writes `site/artifact.html`, the same page without the document
   skeleton, for publishing as a Claude artifact. Filters: region, stage, min and
   max size ($/£/€ one for one), funds I follow, AI-native, studios, search.

### Identity rules (the part that matters most)

- A company is keyed on its domain when known, else its normalised name. Learning
  the domain later **merges** rather than duplicating.
- A name that is another name plus a qualifier ("Kasvu" / "Kasvu Therapeutics") is
  the same company **only when the two also share a round of the same size at the
  same time**. Whole words only, so "Meta" never swallows "Metabolic". Two
  companies with their own distinct domains are never folded together.
- A round is keyed on company plus month, matched within a ±45-day window. Stage
  is deliberately not part of the key: outlets disagree about it.
- A round keeps the **earliest** date it was reported on.
- Scores are kept as history in their own table, not as columns on the round, so
  the rubric can change without losing what it said before.

## Commands

```bash
python -m src.funding_radar.cli run          # discover, extract, qualify, store
python -m src.funding_radar.cli run --dry-run  # discovery only, no model calls
python -m src.funding_radar.cli rounds --all # print stored rounds
python -m src.funding_radar.cli sources      # per-source health from the last run
python -m src.funding_radar.cli dedupe       # fold companies stored under two names
python -m src.funding_radar.cli recheck      # re-apply the rules; no model calls
python -m src.funding_radar.cli describe     # 1-2 sentences per in-brief company (cents)
python -m src.funding_radar.cli site --open  # build the page
python -m src.funding_radar.cli test-llm     # check the key and model
python scripts/accuracy_check.py             # score against the labelled set (a few cents)
python scripts/recall_check.py               # what the pipeline misses
python scripts/data_audit.py                 # amount / link / description coverage
python scripts/verify_sources.py             # candidate sources (run it on the runner)
python scripts/search_probe.py               # web search vs Google News (spends ~$0.25)
pytest -q                                    # 277 tests, offline; browser tests need Playwright
```

## Operations

- **Schedule:** `.github/workflows/scan.yml`, 07:00 and 17:00 London. Four crons
  cover both UTC offsets and `scripts/scan_gate.sh` drops the wrong one, judging by
  **which cron fired**, not the clock. GitHub starts scheduled runs hours late; the
  first gate read the clock and silently skipped every run from 25 to 27 September.
- **Database:** `data/funding.db`, ~1 MB, persisted on the orphan `data` branch,
  force-pushed as a single commit per run so the repo never accumulates copies.
  Move to Turso if it reaches tens of MB.
- **Secret:** `ANTHROPIC_API_KEY`. The owner sets keys themselves; never ask for
  one, read one, or echo one.
- **Tests** run on every push, including the browser tests (Chromium in CI).
- **Probe:** `.github/workflows/probe.yml` runs the source check, a discovery dry
  run, describe + audit on a copy of the database, and the search probe on the
  runner. It never writes the database. Anything that must work from GitHub's IP
  is measured there, not from a laptop or a sandbox.

## State

Built: discovery, extraction, qualification, storage, dedupe, the page, the
schedule, a 20-article labelled accuracy set, a recall check.

**Not built yet — scoring (the next real feature).** Claude Opus reads each
qualified round and returns a fit score (does this company need fractional product,
pricing or growth leadership *now*), an angle (which of the three offers), and a
one-line reason. The `scores` and `feedback` tables and `db.add_score` already
exist and are unused. Then: an 08:00 Telegram digest of the top 3 by fit score,
and a list of companies to suggest to the owner's separate job scanner (with her
approval, never automatic).

## Known issues, measured rather than guessed

- **Finsmes is IP-blocked on GitHub's runners.** It serves this laptop 30 entries
  and refuses the runner whatever user agent asks. Reading it through a Google News
  `site:` search was tried and removed: Google strips the headline out of those
  results. Measured cost: Finsmes is the only outlet for 13 of 127 rounds, and one
  of those is in brief.
- **Atomico (429) and Speedinvest (403)** fail on the runners too.
- **GDELT** rate limits aggressively; paced at one call per 6 seconds, still
  throttled after bursts. Treat as best-effort.
- **UK coverage is thin**: 5 of 36 in-brief rounds under the 2M floor; 7 of 48
  after the floor was removed (database of 2026-09-24). Business Cloud and
  FinTech Global were added for it.
- **Anthropic web search (measured 2026-09-27, 6 UK queries):** $0.24 a run, 51
  results but mostly evergreen pages ("30 best Series B investors"), so 1 new
  in-brief UK round (Ryft). The same queries through Google News: free, 1 new. Off
  in the scan (`web_search.enabled`). It does compose with structured outputs.
- **Tracked funds' own sites** do not announce portfolio rounds (runner check):
  Antler and EF pages are undated link lists; Techstars has no feed. Their rounds
  come through the press: four Google News queries, and the "Funds I follow" chip.
- **Region from a headline** can be wrong: a round with no location in the
  headline tends to come out as "other" and so out of brief.
- Extraction scores **62/63** on the labelled set. The one miss is a headline that
  names no company.
- Stage is often unstated in headlines; those rounds qualify on size alone.

## Sources: what was checked (2026-09-27)

Every URL in `config/sources.yaml` was fetched from the GitHub runner before it went
in (`scripts/verify_sources.py`, candidates in `config/candidate_sources.yaml`).
Seven of fifteen earlier invented VC URLs 404'd, so nothing goes in unverified.

- Added: Business Cloud, FinTech Global (current, UK funding stories).
- Refused on the runner: Tech Funding News (403), Finsmes (403), IT Brief (406),
  Business Weekly and Atomico (429), Speedinvest (403). Broken feeds: FF News,
  Beauhurst, Insider Media. AltFi empty; Prolific North stale since 2025.
- Anthropic web search reaches Finsmes, but see Known issues for its yield.
- Not pursued, by the owner's choice: Companies House, Tavily, OpenAI search.
- Still open: Dealroom and Tracxn, press-release wires, more non-English locales.

## Working agreements

- Tests before claims. Everything in `tests/` runs offline; live checks are marked.
- Measure rather than assert: when something looks broken, query the database and
  say what the number is.
- Report failures plainly, including my own. Several bugs here were mine and the
  commit messages say so.
- The owner reads the data and spots real problems in it. Show her the data early.
