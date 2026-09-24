# Funding Radar

Daily list of newly funded UK and European companies, scored as prospects for a
fractional product, pricing and growth practice.

- **Scope:** UK and Europe, pre-seed to Series B, 2M+ as reported (USD, GBP or EUR),
  rolling 120-day window.
- **Output:** a password-protected static site, plus a Telegram digest at 08:00 with
  the day's three best prospects.
- **Two tiers:** a round is *confirmed* once the company resolves to a real website
  and the amount and stage are specific; everything else is kept as *unconfirmed*
  behind a filter.

## Pipeline

| Stage | What happens |
|---|---|
| Discovery | RSS feeds, Google News queries, GDELT, 15 VC announcement pages, Companies House |
| Cheap filters | URL and headline dedupe, date window, keyword gate — before any model call |
| Extraction | Claude Sonnet: company, amount, stage, investors, location, sector, AI-native, plus the sentence the amount came from |
| Resolution | Match to an existing round by domain, else by normalised name within ±45 days |
| Scoring | Claude Opus on survivors: fit score, one-line reason, and angle (product leadership / pricing / growth) |
| Publish | Encrypted site bundle, Telegram digest, job-scanner suggestions |

## Data model

A company is the durable thing, a round is an event against it, an article is
evidence for a round, and a score is an opinion about it at a point in time.

| Table | Holds | Why separate |
|---|---|---|
| `companies` | Canonical name, aliases, domain, HQ, sector, AI-native | A company raises more than once, and later rounds should enrich one record rather than copy it |
| `rounds` | Stage, amount, currency, date, confidence, confirmed, qualified | One row per raise |
| `investors`, `round_investors` | Who backed it, who led | Lets rules key on the investor (Antler and EF lower the size floor) and answers "show me everything Antler backed" |
| `round_sources` | Every article reporting the round, with the amount it stated | Corroboration, and how disagreements over the amount surface |
| `articles` | Everything seen, and what happened to it | Stops the same story being paid for twice |
| `scores` | Fit, angle, reason, model, rubric version | History: re-scoring adds a row, so a rubric change never erases what it said before |
| `feedback` | Useful / not useful per round | Feeds later scoring |
| `source_health`, `runs` | Per-source yield and run stats | A scraper that breaks shows up as "quiet", not as a silently shorter list |

Identity rules, which is where this kind of pipeline usually rots:

- A company is keyed on its **domain** when known, else its **normalised name**.
  When a domain turns up later, the earlier record is **merged**, not duplicated.
- A round is keyed on **company plus month**, and matched within a **±45-day window**,
  so one raise reported on 30 September and again on 2 October stays one round.
- **Stage is not part of the key.** Outlets disagree about seed versus Series A, and
  a relabelled round is still the same round.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env     # set ANTHROPIC_API_KEY and the Telegram values
pytest                   # offline tests
pytest -m live           # hits the real sources
```

Configuration lives in [`config/sources.yaml`](config/sources.yaml): sources, filters,
the fund list, and the fixed sector list the model assigns from.

## Accuracy

`tests/fixtures/labelled_articles.json` holds 20 real articles labelled by hand from
the article text (not from model output), including debt, valuation stories, list
articles and one round reported twice in two currencies.

```bash
pytest tests/test_accuracy.py      # offline: the set and the scorer itself
python scripts/accuracy_check.py   # live: a few cents, prints a scorecard
```

A null label means the headline does not support an answer, so that field is not
scored. Rows marked `judgement` are reported but excluded from the score: their
right answer is a preference, not a fact.
