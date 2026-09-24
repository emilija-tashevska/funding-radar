-- Funding Radar schema.
--
-- Shape: a company is the durable thing, a round is an event against it, and an
-- article is evidence for a round. Scores are kept as history rather than columns
-- on the round, so the model or rubric can change without losing what it said before.

CREATE TABLE IF NOT EXISTS companies (
    company_id   TEXT PRIMARY KEY,          -- domain when known, else normalised name
    canonical_name TEXT NOT NULL,
    name_key     TEXT NOT NULL,             -- normalised name used for matching
    domain       TEXT DEFAULT '',
    aliases      TEXT DEFAULT '[]',         -- other name_keys seen for this company
    summary      TEXT DEFAULT '',
    hq_city      TEXT DEFAULT '',
    hq_country   TEXT DEFAULT '',
    region       TEXT DEFAULT '',           -- uk | europe | us | other
    sector       TEXT DEFAULT '',
    ai_native    INTEGER DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_companies_name_key ON companies(name_key);
CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);

CREATE TABLE IF NOT EXISTS rounds (
    round_id     TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
    stage        TEXT DEFAULT '',           -- normalised: pre-seed | seed | series a ...
    stage_raw    TEXT DEFAULT '',
    amount_value REAL,                      -- as reported, in `currency`
    currency     TEXT DEFAULT '',
    amount_text  TEXT DEFAULT '',
    amount_usd   REAL,                      -- rough, only for the size cut-off
    announced_date TEXT,
    summary      TEXT DEFAULT '',
    evidence     TEXT DEFAULT '',           -- the sentence the amount came from
    confidence   REAL DEFAULT 0,
    confirmed    INTEGER DEFAULT 0,
    amount_disputed INTEGER DEFAULT 0,
    qualified    INTEGER DEFAULT 0,         -- inside the brief (stage/size/region)
    qualified_reason TEXT DEFAULT '',       -- why not, so the site can filter rather than hide
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT,
    digest_sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rounds_company ON rounds(company_id, announced_date);
CREATE INDEX IF NOT EXISTS idx_rounds_date ON rounds(announced_date DESC);

CREATE TABLE IF NOT EXISTS investors (
    investor_id  TEXT PRIMARY KEY,          -- normalised name
    name         TEXT NOT NULL,
    tracked      INTEGER DEFAULT 0,         -- one of the funds we follow
    min_amount   REAL                       -- per-investor size floor, e.g. Antler
);

CREATE TABLE IF NOT EXISTS round_investors (
    round_id     TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
    investor_id  TEXT NOT NULL REFERENCES investors(investor_id) ON DELETE CASCADE,
    is_lead      INTEGER DEFAULT 0,
    PRIMARY KEY (round_id, investor_id)
);

CREATE TABLE IF NOT EXISTS round_sources (
    round_id     TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
    article_url  TEXT NOT NULL,
    outlet       TEXT DEFAULT '',
    source_kind  TEXT DEFAULT '',
    title        TEXT DEFAULT '',
    published_at TEXT,
    amount_value REAL,                      -- what this outlet reported
    currency     TEXT DEFAULT '',           -- and in which currency: outlets convert
    seen_at      TEXT NOT NULL,
    PRIMARY KEY (round_id, article_url)
);

CREATE TABLE IF NOT EXISTS articles (
    article_id   TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT DEFAULT '',
    title_key    TEXT DEFAULT '',
    company_hint TEXT DEFAULT '',
    outcome      TEXT DEFAULT '',           -- extracted | off_topic | stale | rejected | duplicate
    round_id     TEXT,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_title_key ON articles(title_key);

-- Score history: a re-score adds a row rather than overwriting the last verdict.
CREATE TABLE IF NOT EXISTS scores (
    score_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id     TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE,
    fit_score    REAL,
    angle        TEXT DEFAULT '',           -- product leadership | pricing | growth
    reason       TEXT DEFAULT '',
    model        TEXT DEFAULT '',
    rubric_version TEXT DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_round ON scores(round_id, created_at DESC);

CREATE TABLE IF NOT EXISTS feedback (
    round_id     TEXT PRIMARY KEY REFERENCES rounds(round_id) ON DELETE CASCADE,
    verdict      TEXT,                      -- useful | not_useful
    note         TEXT DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health (
    source       TEXT PRIMARY KEY,
    source_kind  TEXT DEFAULT '',
    last_run_at  TEXT,
    items_found  INTEGER DEFAULT 0,
    previous_items_found INTEGER,
    median_items REAL,
    status       TEXT DEFAULT '',           -- ok | quiet | failed
    error        TEXT DEFAULT '',
    consecutive_failures INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT,
    finished_at  TEXT,
    stats        TEXT
);
