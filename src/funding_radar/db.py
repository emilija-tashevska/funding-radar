"""SQLite store: rounds, the articles that evidence them, and source health."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.funding_radar.models import Round


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FundingDatabase:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FundingDatabase":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rounds (
                round_id TEXT PRIMARY KEY,
                company TEXT NOT NULL,
                company_key TEXT NOT NULL,
                company_domain TEXT,
                summary TEXT,
                stage TEXT,
                amount_text TEXT,
                amount_value REAL,
                currency TEXT,
                round_date TEXT,
                investors TEXT,
                hq_city TEXT,
                hq_country TEXT,
                region TEXT,
                sector TEXT,
                ai_native INTEGER DEFAULT 0,
                evidence TEXT,
                confidence REAL,
                confirmed INTEGER DEFAULT 0,
                amount_disputed INTEGER DEFAULT 0,
                fit_score REAL,
                fit_reason TEXT,
                angle TEXT,
                feedback TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT,
                digest_sent_at TEXT
            );

            CREATE TABLE IF NOT EXISTS round_sources (
                round_id TEXT NOT NULL,
                article_url TEXT NOT NULL,
                source TEXT,
                source_kind TEXT,
                title TEXT,
                published_at TEXT,
                amount_value REAL,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (round_id, article_url)
            );

            CREATE TABLE IF NOT EXISTS seen_articles (
                article_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title_key TEXT,
                first_seen_at TEXT NOT NULL,
                outcome TEXT
            );

            CREATE TABLE IF NOT EXISTS source_health (
                source TEXT PRIMARY KEY,
                source_kind TEXT,
                last_run_at TEXT,
                items_found INTEGER DEFAULT 0,
                previous_items_found INTEGER,
                median_items REAL,
                status TEXT,
                error TEXT,
                consecutive_failures INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT,
                finished_at TEXT,
                stats TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_rounds_date ON rounds(round_date DESC);
            CREATE INDEX IF NOT EXISTS idx_rounds_company ON rounds(company_key, round_date);
            CREATE INDEX IF NOT EXISTS idx_seen_title ON seen_articles(title_key);
            """
        )
        self._conn.commit()

    # ---- articles -------------------------------------------------------

    def is_article_seen(self, article_id: str, title_key: str) -> bool:
        """True when this URL, or an article with the same normalised title, was handled."""
        row = self._conn.execute(
            "SELECT 1 FROM seen_articles WHERE article_id = ? OR (title_key = ? AND title_key != '')",
            (article_id, title_key),
        ).fetchone()
        return row is not None

    def record_article(self, article_id: str, url: str, title_key: str, outcome: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO seen_articles (article_id, url, title_key, first_seen_at, outcome) "
            "VALUES (?, ?, ?, COALESCE((SELECT first_seen_at FROM seen_articles WHERE article_id = ?), ?), ?)",
            (article_id, url, title_key, article_id, _now(), outcome),
        )
        self._conn.commit()

    # ---- rounds ---------------------------------------------------------

    def get_round(self, round_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM rounds WHERE round_id = ?", (round_id,)).fetchone()
        return dict(row) if row else None

    def find_round_by_company(self, domain: str, name_key: str, within_days: int, around: str) -> dict | None:
        """Match an existing round for the same company near the same date.

        Catches the same raise reported either side of a month boundary, which a
        month-keyed id alone would split into two rows.
        """
        try:
            centre = datetime.fromisoformat((around or "").replace("Z", "+00:00"))
        except ValueError:
            return None
        low = (centre - timedelta(days=within_days)).isoformat()
        high = (centre + timedelta(days=within_days)).isoformat()
        row = self._conn.execute(
            """
            SELECT * FROM rounds
            WHERE ((company_domain != '' AND company_domain = ?) OR company_key = ?)
              AND round_date BETWEEN ? AND ?
            ORDER BY round_date
            """,
            (domain, name_key, low, high),
        ).fetchone()
        return dict(row) if row else None

    def upsert_round(self, round_: Round) -> bool:
        """Insert a round or refresh it. Returns True when it was new."""
        existing = self.get_round(round_.round_id)
        now = _now()
        if existing:
            self._conn.execute(
                """
                UPDATE rounds SET
                    company = ?, company_domain = ?, summary = ?, stage = ?,
                    amount_text = ?, amount_value = ?, currency = ?, round_date = ?,
                    investors = ?, hq_city = ?, hq_country = ?, region = ?, sector = ?,
                    ai_native = ?, evidence = ?, confidence = ?, confirmed = ?,
                    amount_disputed = ?, last_seen_at = ?
                WHERE round_id = ?
                """,
                (
                    round_.company, round_.company_domain, round_.summary, round_.stage,
                    round_.amount_text, round_.amount_value, round_.currency, round_.round_date,
                    round_.investors, round_.hq_city, round_.hq_country, round_.region,
                    round_.sector, int(round_.ai_native), round_.evidence, round_.confidence,
                    int(round_.confirmed), int(round_.amount_disputed), now, round_.round_id,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO rounds (
                    round_id, company, company_key, company_domain, summary, stage,
                    amount_text, amount_value, currency, round_date, investors,
                    hq_city, hq_country, region, sector, ai_native, evidence, confidence,
                    confirmed, amount_disputed, fit_score, fit_reason, angle,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_.round_id, round_.company, round_.company_key, round_.company_domain,
                    round_.summary, round_.stage, round_.amount_text, round_.amount_value,
                    round_.currency, round_.round_date, round_.investors, round_.hq_city,
                    round_.hq_country, round_.region, round_.sector, int(round_.ai_native),
                    round_.evidence, round_.confidence, int(round_.confirmed),
                    int(round_.amount_disputed), round_.fit_score, round_.fit_reason,
                    round_.angle, now, now,
                ),
            )
        self._conn.commit()
        return existing is None

    def add_round_source(self, round_id: str, article, amount_value: float | None) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO round_sources
                (round_id, article_url, source, source_kind, title, published_at, amount_value, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, article.url, article.source, article.source_kind, article.title,
             article.published_at, amount_value, _now()),
        )
        self._conn.commit()

    def round_source_amounts(self, round_id: str) -> list[float]:
        rows = self._conn.execute(
            "SELECT amount_value FROM round_sources WHERE round_id = ? AND amount_value IS NOT NULL",
            (round_id,),
        ).fetchall()
        return [row["amount_value"] for row in rows]

    def set_scores(self, round_id: str, *, fit_score: float, fit_reason: str, angle: str) -> None:
        self._conn.execute(
            "UPDATE rounds SET fit_score = ?, fit_reason = ?, angle = ? WHERE round_id = ?",
            (fit_score, fit_reason, angle, round_id),
        )
        self._conn.commit()

    def list_rounds(self, *, window_days: int) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT * FROM rounds
            WHERE COALESCE(round_date, first_seen_at) >= ?
            ORDER BY COALESCE(round_date, first_seen_at) DESC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    def undigested_rounds(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM rounds WHERE digest_sent_at IS NULL AND confirmed = 1 AND fit_score IS NOT NULL "
            "ORDER BY fit_score DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_digested(self, round_ids: list[str]) -> None:
        if not round_ids:
            return
        placeholders = ",".join("?" * len(round_ids))
        self._conn.execute(
            f"UPDATE rounds SET digest_sent_at = ? WHERE round_id IN ({placeholders})",
            (_now(), *round_ids),
        )
        self._conn.commit()

    # ---- source health --------------------------------------------------

    def record_source(self, source: str, kind: str, items: int, *, error: str = "") -> dict | None:
        """Store this run's yield and flag a source that has gone quiet or failed."""
        previous = self._conn.execute(
            "SELECT items_found, median_items, consecutive_failures FROM source_health WHERE source = ?",
            (source,),
        ).fetchone()
        previous_items = previous["items_found"] if previous else None
        median = previous["median_items"] if previous and previous["median_items"] else None
        median = items if median is None else round((median * 0.8) + (items * 0.2), 2)
        failures = (previous["consecutive_failures"] if previous else 0) + 1 if error else 0
        # A source that usually yields plenty and suddenly yields nothing is broken,
        # not quiet; that is how a scraper breaks without anyone noticing.
        quiet = bool(not error and items == 0 and previous_items)
        status = "failed" if error else ("quiet" if quiet else "ok")
        self._conn.execute(
            """
            INSERT INTO source_health (source, source_kind, last_run_at, items_found,
                previous_items_found, median_items, status, error, consecutive_failures)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                source_kind = excluded.source_kind,
                last_run_at = excluded.last_run_at,
                previous_items_found = source_health.items_found,
                items_found = excluded.items_found,
                median_items = excluded.median_items,
                status = excluded.status,
                error = excluded.error,
                consecutive_failures = excluded.consecutive_failures
            """,
            (source, kind, _now(), items, previous_items, median, status, error[:500], failures),
        )
        self._conn.commit()
        if status == "ok":
            return None
        return {"source": source, "status": status, "items": items, "error": error, "median": median}

    def list_source_health(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
        return [dict(row) for row in rows]

    def record_run(self, run_id: str, started_at: str, stats: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
            (run_id, started_at, _now(), json.dumps(stats)),
        )
        self._conn.commit()
