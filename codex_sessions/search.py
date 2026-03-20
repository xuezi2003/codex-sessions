from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .models import SEARCH_DB_NAME, SEARCH_SCHEMA_VERSION, SessionRecord
from .store import build_search_document, sanitize_inline, sanitize_screen_text


def quote_fts_term(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def search_fingerprint(record: SessionRecord, stat: os.stat_result) -> str:
    payload = [
        str(record.rollout_path),
        stat.st_mtime_ns,
        stat.st_size,
        record.updated_at,
        record.created_at,
        record.title,
        record.cwd,
        record.model_provider,
        int(record.archived),
        record.last_preview,
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class SearchIndex:
    def __init__(self, codex_home: Path) -> None:
        self.db_path = codex_home / SEARCH_DB_NAME
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        version = self.conn.execute(
            "SELECT value FROM search_meta WHERE key = 'schema_version'"
        ).fetchone()
        if version is not None and version["value"] != str(SEARCH_SCHEMA_VERSION):
            self.conn.execute("DROP TABLE IF EXISTS search_cache")
            self.conn.execute("DROP TABLE IF EXISTS search_fts")
            self.conn.execute("DELETE FROM search_meta WHERE key = 'schema_version'")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_cache (
                session_id TEXT PRIMARY KEY,
                updated_at INTEGER NOT NULL,
                rollout_path TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                title TEXT NOT NULL,
                search_text TEXT NOT NULL,
                search_text_folded TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS search_fts
            USING fts5(
                session_id UNINDEXED,
                search_text,
                tokenize='trigram'
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO search_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(SEARCH_SCHEMA_VERSION),),
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_search_cache_updated
            ON search_cache(updated_at DESC, session_id DESC)
            """
        )
        self.conn.commit()

    def sync_sessions(self, sessions: list[SessionRecord]) -> None:
        existing = {
            row["session_id"]: row["fingerprint"]
            for row in self.conn.execute(
                "SELECT session_id, fingerprint FROM search_cache"
            )
        }
        current_ids = {record.session_id for record in sessions}
        stale_ids = set(existing) - current_ids

        for session_id in stale_ids:
            self.conn.execute("DELETE FROM search_cache WHERE session_id = ?", (session_id,))
            self.conn.execute("DELETE FROM search_fts WHERE session_id = ?", (session_id,))

        for record in sessions:
            try:
                stat = record.rollout_path.stat()
            except OSError:
                continue
            fingerprint = search_fingerprint(record, stat)
            if existing.get(record.session_id) == fingerprint:
                continue

            search_text = sanitize_screen_text(build_search_document(record))
            self.conn.execute("DELETE FROM search_cache WHERE session_id = ?", (record.session_id,))
            self.conn.execute("DELETE FROM search_fts WHERE session_id = ?", (record.session_id,))
            self.conn.execute(
                """
                INSERT INTO search_cache(
                    session_id,
                    updated_at,
                    rollout_path,
                    fingerprint,
                    title,
                    search_text,
                    search_text_folded
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.updated_at,
                    str(record.rollout_path),
                    fingerprint,
                    record.title,
                    search_text,
                    search_text.casefold(),
                ),
            )
            self.conn.execute(
                "INSERT INTO search_fts(session_id, search_text) VALUES (?, ?)",
                (record.session_id, search_text),
            )
        self.conn.commit()

    def search(self, query: str) -> list[str]:
        normalized = sanitize_inline(query).strip()
        if not normalized:
            return []

        terms = [term for term in normalized.casefold().split() if term]
        if not terms:
            return []

        if all(len(term) >= 3 for term in terms):
            match_expr = " AND ".join(quote_fts_term(term) for term in terms)
            rows = self.conn.execute(
                """
                SELECT cache.session_id
                FROM search_fts AS fts
                JOIN search_cache AS cache ON cache.session_id = fts.session_id
                WHERE search_fts MATCH ?
                ORDER BY cache.updated_at DESC, cache.session_id DESC
                """,
                (match_expr,),
            ).fetchall()
            return [row["session_id"] for row in rows]

        where_clause = " AND ".join("instr(search_text_folded, ?) > 0" for _ in terms)
        rows = self.conn.execute(
            f"""
            SELECT session_id
            FROM search_cache
            WHERE {where_clause}
            ORDER BY updated_at DESC, session_id DESC
            """,
            terms,
        ).fetchall()
        return [row["session_id"] for row in rows]
