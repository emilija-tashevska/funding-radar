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
