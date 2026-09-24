"""The store. Companies are durable, rounds are events against them, articles are evidence.

Identity is the hard part of this pipeline, so it lives here rather than being
scattered: a company is keyed by domain when one is known and by normalised name
otherwise, and `merge_companies` exists because a domain often turns up only after
we have already seen the company by name.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from src.funding_radar.models import Round, normalize_company, normalize_domain

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
# How far apart two reports of the same raise can sit and still be one round.
SAME_ROUND_WINDOW_DAYS = 45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def company_id_for(domain: str, name: str) -> str:
    """Domain when we have one, else the normalised name. Stable across runs."""
    domain = normalize_domain(domain)
    return f"d:{domain}" if domain else f"n:{normalize_company(name)}"


class FundingDatabase:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that CREATE TABLE IF NOT EXISTS will not add to an old file."""
        for table, column, definition in (("round_sources", "currency", "TEXT DEFAULT ''"),):
            existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FundingDatabase":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ---- companies ------------------------------------------------------

    def upsert_company(self, round_: Round) -> str:
        """Insert or update the company behind a round; returns its id.

        Matching order: the id itself, then the domain, then any known alias. A
        company first seen by name and later by domain is merged, not duplicated.
        """
        name_key = normalize_company(round_.company)
        domain = normalize_domain(round_.company_domain)
        new_id = company_id_for(domain, round_.company)
        now = _now()

        existing = self._conn.execute(
            "SELECT * FROM companies WHERE company_id = ?", (new_id,)
        ).fetchone()
        if not existing and domain:
            existing = self._conn.execute(
                "SELECT * FROM companies WHERE domain = ? AND domain != ''", (domain,)
            ).fetchone()
        if not existing and name_key:
            existing = self._conn.execute(
                "SELECT * FROM companies WHERE name_key = ? OR aliases LIKE ?",
                (name_key, f'%"{name_key}"%'),
            ).fetchone()

        if existing is None:
            self._conn.execute(
                """
                INSERT INTO companies (company_id, canonical_name, name_key, domain, aliases,
                    summary, hq_city, hq_country, region, sector, ai_native, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id, round_.company, name_key, domain, json.dumps([name_key]), round_.summary,
                 round_.hq_city, round_.hq_country, round_.region, round_.sector,
                 int(round_.ai_native), now, now),
            )
            self._conn.commit()
            return new_id

        company_id = existing["company_id"]
        # Learning the domain upgrades the identity, so move the old rows across.
        if domain and existing["company_id"] != new_id:
            self.merge_companies(existing["company_id"], new_id, round_)
            company_id = new_id
        aliases = set(json.loads(existing["aliases"] or "[]")) | {name_key}
        self._conn.execute(
            """
            UPDATE companies SET
                canonical_name = COALESCE(NULLIF(?, ''), canonical_name),
                domain = COALESCE(NULLIF(?, ''), domain),
                aliases = ?,
                summary = COALESCE(NULLIF(?, ''), summary),
                hq_city = COALESCE(NULLIF(?, ''), hq_city),
                hq_country = COALESCE(NULLIF(?, ''), hq_country),
                region = COALESCE(NULLIF(?, ''), region),
                sector = COALESCE(NULLIF(?, ''), sector),
                ai_native = MAX(ai_native, ?),
                last_seen_at = ?
            WHERE company_id = ?
            """,
            (round_.company, domain, json.dumps(sorted(aliases)), round_.summary, round_.hq_city,
             round_.hq_country, round_.region, round_.sector, int(round_.ai_native), now, company_id),
        )
        self._conn.commit()
        return company_id

    def merge_companies(self, old_id: str, new_id: str, round_: Round) -> None:
        """Re-key a company (and its rounds) once a better identity is known."""
        old = self._conn.execute("SELECT * FROM companies WHERE company_id = ?", (old_id,)).fetchone()
        if old is None or old_id == new_id:
            return
        aliases = set(json.loads(old["aliases"] or "[]")) | {old["name_key"], normalize_company(round_.company)}
        self._conn.execute(
            """
            INSERT INTO companies (company_id, canonical_name, name_key, domain, aliases, summary,
                hq_city, hq_country, region, sector, ai_native, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id) DO UPDATE SET aliases = excluded.aliases
            """,
            (new_id, old["canonical_name"], old["name_key"], normalize_domain(round_.company_domain),
             json.dumps(sorted(aliases)), old["summary"], old["hq_city"], old["hq_country"],
             old["region"], old["sector"], old["ai_native"], old["first_seen_at"], _now()),
        )
        self._conn.execute("UPDATE rounds SET company_id = ? WHERE company_id = ?", (new_id, old_id))
        self._conn.execute("DELETE FROM companies WHERE company_id = ?", (old_id,))
        self._conn.commit()

    def get_company(self, company_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM companies WHERE company_id = ?", (company_id,)).fetchone()
        return dict(row) if row else None

    def find_company(self, domain: str, name: str) -> dict | None:
        domain, name_key = normalize_domain(domain), normalize_company(name)
        if domain:
            row = self._conn.execute(
                "SELECT * FROM companies WHERE domain = ? AND domain != ''", (domain,)
            ).fetchone()
            if row:
                return dict(row)
        row = self._conn.execute(
            "SELECT * FROM companies WHERE name_key = ? OR aliases LIKE ?",
            (name_key, f'%"{name_key}"%'),
        ).fetchone()
        return dict(row) if row else None

    # ---- rounds ---------------------------------------------------------

    def find_round(self, company_id: str, stage: str, announced_date: str | None) -> dict | None:
        """An existing round for this company near this date.

        Matching on the date window rather than the calendar month is what keeps a
        raise reported on 30 September and again on 2 October as one round.
        """
        rows = self._conn.execute(
            "SELECT * FROM rounds WHERE company_id = ? ORDER BY announced_date DESC", (company_id,)
        ).fetchall()
        if not rows:
            return None
        target = _parse(announced_date)
        for row in rows:
            other = _parse(row["announced_date"])
            if target and other:
                if abs((target - other).days) <= SAME_ROUND_WINDOW_DAYS:
                    return dict(row)
            elif stage and row["stage"] and stage.lower() == row["stage"].lower():
                # No usable dates: same company at the same stage is one round.
                return dict(row)
        return None

    def upsert_round(self, round_: Round, company_id: str) -> tuple[str, bool]:
        """Insert or refresh a round. Returns (round_id, created)."""
        existing = self.find_round(company_id, round_.stage, round_.round_date)
        now = _now()
        if existing:
            round_id = existing["round_id"]
            self._conn.execute(
                """
                UPDATE rounds SET
                    stage = COALESCE(NULLIF(?, ''), stage),
                    stage_raw = COALESCE(NULLIF(?, ''), stage_raw),
                    amount_value = COALESCE(?, amount_value),
                    currency = COALESCE(NULLIF(?, ''), currency),
                    amount_text = COALESCE(NULLIF(?, ''), amount_text),
                    amount_usd = COALESCE(?, amount_usd),
                    announced_date = COALESCE(announced_date, ?),
                    summary = COALESCE(NULLIF(?, ''), summary),
                    evidence = COALESCE(NULLIF(?, ''), evidence),
                    confidence = MAX(confidence, ?),
                    confirmed = MAX(confirmed, ?),
                    amount_disputed = ?,
                    qualified = ?,
                    last_seen_at = ?
                WHERE round_id = ?
                """,
                (round_.stage, round_.stage_raw, round_.amount_value, round_.currency,
                 round_.amount_text, round_.amount_usd, round_.round_date, round_.summary,
                 round_.evidence, round_.confidence, int(round_.confirmed),
                 int(round_.amount_disputed), int(round_.qualified), now, round_id),
            )
            self._conn.commit()
            return round_id, False

        round_id = round_.round_id_for(company_id)
        self._conn.execute(
            """
            INSERT INTO rounds (round_id, company_id, stage, stage_raw, amount_value, currency,
                amount_text, amount_usd, announced_date, summary, evidence, confidence,
                confirmed, amount_disputed, qualified, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, company_id, round_.stage, round_.stage_raw, round_.amount_value,
             round_.currency, round_.amount_text, round_.amount_usd, round_.round_date,
             round_.summary, round_.evidence, round_.confidence, int(round_.confirmed),
             int(round_.amount_disputed), int(round_.qualified), now, now),
        )
        self._conn.commit()
        return round_id, True

    def get_round(self, round_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM rounds WHERE round_id = ?", (round_id,)).fetchone()
        return dict(row) if row else None

    def set_round_flags(self, round_id: str, *, confirmed: bool | None = None,
                        amount_disputed: bool | None = None, qualified: bool | None = None,
                        qualified_reason: str | None = None) -> None:
        sets, values = [], []
        for column, value in (("confirmed", confirmed), ("amount_disputed", amount_disputed), ("qualified", qualified)):
            if value is not None:
                sets.append(f"{column} = ?")
                values.append(int(value))
        if qualified_reason is not None:
            sets.append("qualified_reason = ?")
            values.append(qualified_reason)
        if not sets:
            return
        values.append(round_id)
        self._conn.execute(f"UPDATE rounds SET {', '.join(sets)} WHERE round_id = ?", values)
        self._conn.commit()

    # ---- investors ------------------------------------------------------

    def upsert_investor(self, name: str, *, tracked: bool = False, min_amount: float | None = None) -> str:
        investor_id = normalize_company(name)
        if not investor_id:
            return ""
        self._conn.execute(
            """
            INSERT INTO investors (investor_id, name, tracked, min_amount) VALUES (?, ?, ?, ?)
            ON CONFLICT(investor_id) DO UPDATE SET
                name = excluded.name,
                tracked = MAX(investors.tracked, excluded.tracked),
                min_amount = COALESCE(excluded.min_amount, investors.min_amount)
            """,
            (investor_id, name.strip(), int(tracked), min_amount),
        )
        self._conn.commit()
        return investor_id

    def link_investors(self, round_id: str, names: Iterable[str], lead: str = "") -> list[str]:
        lead_key = normalize_company(lead)
        linked = []
        for name in names:
            investor_id = self.upsert_investor(name)
            if not investor_id:
                continue
            self._conn.execute(
                "INSERT OR REPLACE INTO round_investors (round_id, investor_id, is_lead) VALUES (?, ?, ?)",
                (round_id, investor_id, int(investor_id == lead_key)),
            )
            linked.append(investor_id)
        self._conn.commit()
        return linked

    def round_investors(self, round_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT i.*, ri.is_lead FROM round_investors ri
            JOIN investors i ON i.investor_id = ri.investor_id
            WHERE ri.round_id = ?
            ORDER BY ri.is_lead DESC, i.name
            """,
            (round_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def tracked_investors(self) -> dict[str, dict]:
        rows = self._conn.execute("SELECT * FROM investors WHERE tracked = 1").fetchall()
        return {row["investor_id"]: dict(row) for row in rows}

    # ---- sources and articles ------------------------------------------

    def add_round_source(self, round_id: str, article, amount_value: float | None = None,
                         currency: str = "") -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO round_sources
                (round_id, article_url, outlet, source_kind, title, published_at,
                 amount_value, currency, seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, article.url, article.source, article.source_kind, article.title,
             article.published_at, amount_value, (currency or "").upper(), _now()),
        )
        self._conn.commit()

    def round_sources(self, round_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM round_sources WHERE round_id = ? ORDER BY seen_at", (round_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def distinct_outlets(self, round_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT outlet) AS n FROM round_sources WHERE round_id = ?", (round_id,)
        ).fetchone()
        return int(row["n"] or 0)

    def is_article_seen(self, article_id: str, title_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM articles WHERE article_id = ? OR (title_key = ? AND title_key != '')",
            (article_id, title_key),
        ).fetchone()
        return row is not None

    def record_article(self, article, title_key: str, outcome: str, *, company_hint: str = "",
                       round_id: str | None = None) -> None:
        self._conn.execute(
            """
            INSERT INTO articles (article_id, url, title, title_key, company_hint, outcome, round_id, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(article_id) DO UPDATE SET
                outcome = excluded.outcome,
                round_id = COALESCE(excluded.round_id, articles.round_id)
            """,
            (article.article_id, article.url, article.title, title_key, company_hint, outcome, round_id, _now()),
        )
        self._conn.commit()

    # ---- scores and feedback -------------------------------------------

    def add_score(self, round_id: str, *, fit_score: float, angle: str, reason: str,
                  model: str, rubric_version: str) -> None:
        self._conn.execute(
            """
            INSERT INTO scores (round_id, fit_score, angle, reason, model, rubric_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (round_id, fit_score, angle, reason, model, rubric_version, _now()),
        )
        self._conn.commit()

    def latest_score(self, round_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM scores WHERE round_id = ? ORDER BY created_at DESC, score_id DESC LIMIT 1",
            (round_id,),
        ).fetchone()
        return dict(row) if row else None

    def set_feedback(self, round_id: str, verdict: str, note: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO feedback (round_id, verdict, note, created_at) VALUES (?, ?, ?, ?)",
            (round_id, verdict, note, _now()),
        )
        self._conn.commit()

    # ---- reading --------------------------------------------------------

    def list_rounds(self, *, window_days: int, qualified_only: bool = False) -> list[dict]:
        """Rounds for the site: company, round, latest score and investors in one row."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        rows = self._conn.execute(
            f"""
            SELECT r.*, c.canonical_name AS company, c.domain, c.summary AS company_summary,
                   c.hq_city, c.hq_country, c.region, c.sector, c.ai_native,
                   s.fit_score, s.angle, s.reason AS fit_reason, f.verdict AS feedback
            FROM rounds r
            JOIN companies c ON c.company_id = r.company_id
            LEFT JOIN scores s ON s.score_id = (
                SELECT score_id FROM scores WHERE round_id = r.round_id
                ORDER BY created_at DESC, score_id DESC LIMIT 1
            )
            LEFT JOIN feedback f ON f.round_id = r.round_id
            WHERE COALESCE(r.announced_date, r.first_seen_at) >= ?
              {"AND r.qualified = 1" if qualified_only else ""}
            ORDER BY COALESCE(r.announced_date, r.first_seen_at) DESC
            """,
            (cutoff,),
        ).fetchall()
        rounds = []
        for row in rows:
            record = dict(row)
            record["investors"] = [i["name"] for i in self.round_investors(record["round_id"])]
            record["sources"] = self.round_sources(record["round_id"])
            rounds.append(record)
        return rounds

    def undigested_rounds(self) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT r.round_id FROM rounds r
            JOIN scores s ON s.round_id = r.round_id
            WHERE r.digest_sent_at IS NULL AND r.qualified = 1 AND r.confirmed = 1
            GROUP BY r.round_id
            ORDER BY MAX(s.fit_score) DESC
            """
        ).fetchall()
        ids = [row["round_id"] for row in rows]
        by_id = {r["round_id"]: r for r in self.list_rounds(window_days=3650)}
        return [by_id[i] for i in ids if i in by_id]

    def mark_digested(self, round_ids: list[str]) -> None:
        if not round_ids:
            return
        placeholders = ",".join("?" * len(round_ids))
        self._conn.execute(
            f"UPDATE rounds SET digest_sent_at = ? WHERE round_id IN ({placeholders})", (_now(), *round_ids)
        )
        self._conn.commit()

    # ---- source health and runs ----------------------------------------

    def record_source(self, source: str, kind: str, items: int, *, error: str = "") -> dict | None:
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
                source_kind = excluded.source_kind, last_run_at = excluded.last_run_at,
                previous_items_found = source_health.items_found, items_found = excluded.items_found,
                median_items = excluded.median_items, status = excluded.status,
                error = excluded.error, consecutive_failures = excluded.consecutive_failures
            """,
            (source, kind, _now(), items, previous_items, median, status, error[:500], failures),
        )
        self._conn.commit()
        return None if status == "ok" else {"source": source, "status": status, "items": items,
                                            "error": error, "median": median}

    def list_source_health(self) -> list[dict]:
        return [dict(row) for row in self._conn.execute("SELECT * FROM source_health ORDER BY source")]

    def record_run(self, run_id: str, started_at: str, stats: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, finished_at, stats) VALUES (?, ?, ?, ?)",
            (run_id, started_at, _now(), json.dumps(stats)),
        )
        self._conn.commit()


def _parse(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
