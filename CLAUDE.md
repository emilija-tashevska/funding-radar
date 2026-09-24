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
| Size floor | 2M, and $2M / £2M / €2M all count as the bar — deliberately no currency conversion for the floor |
| Regions | UK and Europe in brief; US and elsewhere stored and filterable, off by default |
| Window | 120-day rolling history on the page |
| Out-of-brief rounds | **Stored and labelled with the reason, never dropped.** The page filters them; the pipeline keeps them |
| Rumoured or in-progress raises | Excluded ("X seeks $500M") — money in the bank only |
| Venture studios | Real rounds, flagged `is_studio`, filterable on the page |
| Rounds whose headline names no company | Fetch the article body once and re-read before dropping |
| Confirmation | Two tiers. Confirmed = amount + stage + (a resolved domain or two independent outlets). Unconfirmed rounds are shown; the owner is fine with that |
| Tracked investors with lower floors | Antler and Entrepreneur First (300k). Creandum tracked. **Not Y Combinator** — explicitly excluded |
| Enrichment | None for the MVP |
| Passphrase | Was in the original brief; the owner later chose a public page with no gate |

## How it works

Five stages, `src/funding_radar/`:

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
5. **site.py + templates/index.html** — one self-contained page with the data
   inlined. Also writes `site/artifact.html`, the same page without the document
   skeleton, for publishing as a Claude artifact.

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
python -m src.funding_radar.cli site --open  # build the page
python -m src.funding_radar.cli test-llm     # check the key and model
python scripts/accuracy_check.py             # score against the labelled set (a few cents)
python scripts/recall_check.py               # what the pipeline misses
pytest -q                                    # 212 tests, all offline
```

## Operations

- **Schedule:** `.github/workflows/scan.yml`, 07:00 and 17:00 London. Four crons
  cover both UTC offsets and a gate job drops the wrong one, so it does not drift
  when the clocks change.
- **Database:** `data/funding.db`, ~1 MB, persisted on the orphan `data` branch,
  force-pushed as a single commit per run so the repo never accumulates copies.
  Move to Turso if it reaches tens of MB.
- **Secret:** `ANTHROPIC_API_KEY`. The owner sets keys themselves; never ask for
  one, read one, or echo one.
- **Tests** run on every push.

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
- **UK coverage is thin**: 5 of 36 in-brief rounds. This is a source-coverage
  problem, and the owner's practice sells in London.
- Extraction scores **62/63** on the labelled set. The one miss is a headline that
  names no company.
- Stage is often unstated in headlines; those rounds qualify on size alone.

## Next stage: more sources (what the owner asked for)

Open questions, to be explored and **verified live before being added** — every URL
in `config/sources.yaml` was checked by fetching it. Earlier in this project seven
of fifteen invented VC URLs 404'd, so nothing goes in unverified.

- **Anthropic's server-side web search tool** in the Messages API. Plausibly the
  best fix for both the blocked publishers and the thin UK coverage, since the
  search runs on Anthropic's side rather than from GitHub's IP. Check the current
  tool name, whether it can be combined with structured outputs in one call, and
  the per-search price before designing around it.
- **Antler and Entrepreneur First pages** — both are tracked investors with lower
  floors, and both announce their own cohorts and raises.
- **Techstars** — accelerator cohorts. Note most of its rounds fall under the floor;
  worth checking whether the follow-on rounds are what matter.
- Others worth weighing: Companies House SH01 filings (UK share allotments, which
  are facts rather than press releases), Dealroom and Tracxn, Sifted's UK feed,
  UKTN, Business Cloud, Beauhurst, the press-release wires, and more non-English
  locales.

## Working agreements

- Tests before claims. Everything in `tests/` runs offline; live checks are marked.
- Measure rather than assert: when something looks broken, query the database and
  say what the number is.
- Report failures plainly, including my own. Several bugs here were mine and the
  commit messages say so.
- The owner reads the data and spots real problems in it. Show her the data early.
